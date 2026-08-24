"""Stage S11 - bulk metadata retrieve.

Pulls the org's metadata to disk once, so the ~90% of analysis that is really
text search over definitions costs zero API calls. Querying per component would
be O(components) calls and exhausts any org's budget.

Two things make this awkward, both verified rather than assumed:

  * **The CLI cannot be scraped for a token.** ``sf org display --json`` returns
    ``[REDACTED]`` and ``sf org auth show-access-token`` demands an interactive
    confirmation. So Python mints the token and *hands* it to the CLI via
    ``sf org login access-token``. This is also what makes the tool portable: a
    brand-new org needs no prior ``sf`` authentication.

  * **Folder-scoped types cannot be wildcarded.** ``Report``, ``Dashboard``,
    ``EmailTemplate`` and ``Document`` return nothing for ``<members>*</members>``;
    they must be enumerated first. Silently retrieving zero reports would make
    every field referenced only by a report look unused.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from xml.sax.saxutils import escape

import structlog

from app.config import get_settings
from app.salesforce.auth import TokenProvider
from app.salesforce.connections import OrgConnection

log = structlog.get_logger()

#: Types safe to wildcard. Ordered roughly by how often they carry references.
WILDCARD_TYPES: tuple[str, ...] = (
    # Code
    "ApexClass", "ApexTrigger", "ApexPage", "ApexComponent",
    "LightningComponentBundle", "AuraDefinitionBundle", "StaticResource",
    # Schema (CustomObject is a force multiplier: it brings back fields,
    # validation rules, list views, web links, field sets, compact layouts,
    # record types and sharing reasons in one pull)
    "CustomObject", "CustomField", "RecordType", "CompactLayout", "FieldSet",
    "GlobalValueSet", "StandardValueSet", "CustomMetadata", "CustomLabels",
    # UI
    "Layout", "FlexiPage", "QuickAction", "WebLink", "CustomTab",
    "CustomApplication", "PathAssistant", "HomePageLayout",
    # Automation
    "Flow", "FlowDefinition", "Workflow", "ApprovalProcess",
    "AssignmentRules", "EscalationRules", "AutoResponseRules",
    "SharingRules", "DuplicateRule", "MatchingRule",
    # Reporting metadata (the definitions, not the folder-scoped instances).
    # NOTE: the reporting-snapshot type is `AnalyticSnapshot`, not
    # `ReportingSnapshot` as the documentation often implies. An unknown type
    # name fails the ENTIRE manifest chunk, not just that type, so a single
    # wrong name silently costs every other type sharing the chunk.
    "ReportType", "AnalyticSnapshot",
    # Security / integration surface
    "PermissionSet", "PermissionSetGroup", "CustomPermission",
    "PlatformEventChannelMember", "ExternalDataSource", "NamedCredential",
    "RemoteSiteSetting", "ConnectedApp", "CustomNotificationType",
    "LightningMessageChannel", "Letterhead",
)

#: Folder-scoped types, mapped to the folder type that contains them.
#:
#: These need TWO levels of enumeration, which is easy to get wrong: listing
#: `Report` directly returns nothing at all, because reports live inside folders.
#: You must list `ReportFolder` first, then list `Report` within each folder.
#: Getting this wrong retrieves zero reports and makes every field referenced
#: only by a report look unused - a silent, dangerous failure.
FOLDER_SCOPED_TYPES: dict[str, str] = {
    "Report": "ReportFolder",
    "Dashboard": "DashboardFolder",
    "EmailTemplate": "EmailFolder",
    "Document": "DocumentFolder",
}

#: Where each folder-scoped type lands once the retrieve zip is extracted.
#: Used to verify that what we asked for actually arrived -- requesting a type
#: and receiving nothing is indistinguishable from "the org has none" unless
#: the two are compared explicitly.
FOLDER_SCOPED_DIRS: dict[str, str] = {
    "Report": "reports",
    "Dashboard": "dashboards",
    "EmailTemplate": "email",
    "Document": "documents",
}

#: Profiles are retrieved separately: they are large, and in orgs with many of
#: them they can push a single retrieve past the file limit on their own.
HEAVY_TYPES: tuple[str, ...] = ("Profile",)


@dataclass
class RetrieveResult:
    workspace: Path
    manifests: list[Path] = field(default_factory=list)
    types_requested: dict[str, int] = field(default_factory=dict)
    files_retrieved: int = 0
    bytes_retrieved: int = 0
    failed_types: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    @property
    def degraded(self) -> bool:
        return bool(self.failed_types)

    def as_dict(self) -> dict:
        return {
            "workspace": str(self.workspace),
            "manifests": [m.name for m in self.manifests],
            "types_requested": self.types_requested,
            "files_retrieved": self.files_retrieved,
            "bytes_retrieved": self.bytes_retrieved,
            "failed_types": self.failed_types,
            "warnings": self.warnings,
            "degraded": self.degraded,
        }


class CliError(RuntimeError):
    pass


class SalesforceCli:
    """Thin wrapper around the `sf` binary with a per-run, isolated session.

    Every invocation gets its own ``SF_CACHE_DIR`` so concurrent runs against
    different orgs cannot collide, and self-update is disabled - a CLI that
    upgrades itself mid-run would make the results unreproducible.
    """

    def __init__(self, conn: OrgConnection, workspace: Path, tokens: TokenProvider) -> None:
        self._conn = conn
        self._ws = workspace
        self._tokens = tokens
        self._alias = f"sfc-{conn.alias}"
        self._logged_in = False
        s = get_settings()
        self._bin = s.sf_cli_path

    def _env(self, token: str | None = None) -> dict[str, str]:
        env = dict(os.environ)
        env.update({
            "SF_CACHE_DIR": str(self._ws / ".sf"),
            "SF_CONFIG_DIR": str(self._ws / ".sf"),
            "SFDX_DISABLE_TELEMETRY": "true",
            "SF_DISABLE_TELEMETRY": "true",
            # Never let a subprocess upgrade itself mid-run.
            "SF_AUTOUPDATE_DISABLE": "true",
            "SF_DISABLE_AUTOUPDATE": "true",
            "SF_DOMAIN_RETRY": "0",
        })
        if token:
            env["SF_ACCESS_TOKEN"] = token
        return env

    def _ensure_dx_project(self) -> None:
        """Write a minimal sfdx-project.json into the workspace.

        `sf project deploy start` refuses to run outside a Salesforce DX project
        ("does not contain a valid Salesforce DX project"), even for a
        delete-only deployment that touches no local source. `project retrieve`
        has no such requirement, which is why retrieval worked and the delete
        rehearsal did not.

        This is a scaffold for the CLI's benefit, not a real project: the
        workspace is disposable and holds only retrieved metadata.
        """
        proj = self._ws / "sfdx-project.json"
        if proj.exists():
            return
        (self._ws / "force-app").mkdir(parents=True, exist_ok=True)
        proj.write_text(json.dumps({
            "packageDirectories": [{"path": "force-app", "default": True}],
            "namespace": "",
            "sfdcLoginUrl": self._conn.instance_url,
            "sourceApiVersion": self._conn.api_version,
        }, indent=2), encoding="utf-8")

    async def _run(self, args: list[str], *, token: str | None = None,
                   timeout: float = 600.0) -> tuple[int, str, str]:
        cmd = [self._bin, *args]
        log.debug("sf_cli", args=args)
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            env=self._env(token), cwd=str(self._ws),
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except TimeoutError:
            proc.kill()
            raise CliError(f"`sf {' '.join(args[:3])}` timed out after {timeout}s") from None
        return proc.returncode or 0, out.decode(errors="replace"), err.decode(errors="replace")

    async def login(self) -> None:
        """Hand the CLI the token Python already holds.

        This is the portability hinge: without it the CLI would need its own
        prior `sf org login` for every org, which defeats pointing the tool at an
        arbitrary org.
        """
        if self._logged_in:
            return
        token = await self._tokens.token()
        (self._ws / ".sf").mkdir(parents=True, exist_ok=True)
        self._ensure_dx_project()
        code, out, err = await self._run([
            "org", "login", "access-token",
            "--instance-url", self._conn.instance_url,
            "--alias", self._alias, "--no-prompt", "--json",
        ], token=token.value, timeout=120.0)
        if code != 0:
            raise CliError(f"sf org login access-token failed: {(err or out)[:500]}")
        self._logged_in = True
        log.info("sf_cli_session_ready", alias=self._alias)

    async def list_metadata(self, mdtype: str, folder: str | None = None) -> list[dict]:
        """Enumerate members of a metadata type, optionally within a folder."""
        await self.login()
        args = ["org", "list", "metadata", "--metadata-type", mdtype,
                "--target-org", self._alias, "--json"]
        if folder:
            args += ["--folder", folder]
        code, out, err = await self._run(args, timeout=180.0)
        if code != 0:
            # Absent types are normal (an org may simply have no Documents);
            # the caller decides whether that is a gap worth reporting.
            log.info("list_metadata_empty", type=mdtype, detail=(err or out)[:200])
            return []
        try:
            payload = json.loads(out)
        except json.JSONDecodeError:
            return []
        result = payload.get("result") or []
        return result if isinstance(result, list) else [result]

    async def retrieve(self, manifest: Path, target: Path) -> tuple[bool, str]:
        await self.login()
        target.mkdir(parents=True, exist_ok=True)
        code, out, err = await self._run([
            "project", "retrieve", "start",
            "--manifest", str(manifest),
            "--target-metadata-dir", str(target),
            "--target-org", self._alias,
            "--wait", "30", "--json",
        ], timeout=1800.0)
        return code == 0, (err or out)[:1000]

    async def deploy_dry_run(self, manifest_dir: Path) -> tuple[bool, str]:
        """Validate-only destructive deploy. Cannot delete anything.

        ``--dry-run`` makes Salesforce check the request and report the outcome
        without committing it, so this is safe to run against production. It is
        also the only way to get the platform's own dependency checker to tell us
        what still references a component.
        """
        await self.login()
        code, out, err = await self._run([
            "project", "deploy", "start",
            "--manifest", str(manifest_dir / "package.xml"),
            "--post-destructive-changes", str(manifest_dir / "destructiveChangesPost.xml"),
            "--dry-run",
            # NoTestRun keeps the rehearsal cheap; we are asking about metadata
            # dependencies, not about whether tests pass.
            "--test-level", "NoTestRun",
            "--target-org", self._alias,
            "--wait", "20", "--json",
        ], timeout=1200.0)
        return code == 0, (out or err)

    async def logout(self) -> None:
        if not self._logged_in:
            return
        await self._run(["org", "logout", "--target-org", self._alias,
                         "--no-prompt"], timeout=60.0)


def build_manifest(
    path: Path, types: dict[str, list[str]], api_version: str
) -> Path:
    """Write a package.xml. ``["*"]`` means wildcard; a list means enumerated."""
    lines = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<Package xmlns="http://soap.sforce.com/2006/04/metadata">']
    for name, members in sorted(types.items()):
        if not members:
            continue
        lines.append("    <types>")
        for m in members:
            lines.append(f"        <members>{escape(m)}</members>")
        lines.append(f"        <name>{escape(name)}</name>")
        lines.append("    </types>")
    lines.append(f"    <version>{api_version}</version>")
    lines.append("</Package>")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


async def retrieve_all(
    conn: OrgConnection, tokens: TokenProvider, workspace: Path,
    *, chunk_size: int = 12,
    on_progress: "Callable[[str, int, int], None] | None" = None,
) -> RetrieveResult:
    """Retrieve the org's metadata into ``workspace``.

    The manifest is split into chunks because a single wildcard package.xml
    exceeds the retrieve file limit on any sizeable org, and a chunk that fails
    should cost only its own types rather than the whole pull.
    """
    workspace.mkdir(parents=True, exist_ok=True)
    result = RetrieveResult(workspace=workspace)
    cli = SalesforceCli(conn, workspace, tokens)

    # This stage runs for minutes inside CLI subprocesses. Without progress it
    # is indistinguishable from a hang, which is the one thing a live view must
    # never be ambiguous about.
    def tick(label: str, done: int, total: int) -> None:
        if on_progress:
            try:
                on_progress(label, done, total)
            except Exception:  # telemetry must never break the work
                log.debug("retrieve_progress_sink_failed")

    try:
        # --- folder-scoped types need two-level enumeration --------------------
        enumerated: dict[str, list[str]] = {}
        for n_type, (mdtype, folder_type) in enumerate(FOLDER_SCOPED_TYPES.items(), 1):
            tick(f"enumerating {mdtype}", n_type, len(FOLDER_SCOPED_TYPES))
            folders = await cli.list_metadata(folder_type)
            folder_names = sorted(
                {f.get("fullName") for f in folders if f.get("fullName")}
            )
            names: set[str] = set()
            for fname in folder_names:
                members = await cli.list_metadata(mdtype, folder=fname)
                names.update(m["fullName"] for m in members if m.get("fullName"))

            if names:
                enumerated[mdtype] = sorted(names)
            else:
                result.warnings.append(
                    f"{mdtype}: none found across {len(folder_names)} folder(s). "
                    f"If the org has {mdtype.lower()}s, this is a COVERAGE GAP - "
                    "components referenced only there would look unused."
                )
            result.types_requested[mdtype] = len(names)
            log.info("folder_scoped_enumerated", type=mdtype,
                     folders=len(folder_names), members=len(names))

        # --- chunk the wildcard types ----------------------------------------
        wildcard = {t: ["*"] for t in WILDCARD_TYPES}
        for t in WILDCARD_TYPES:
            result.types_requested.setdefault(t, -1)  # -1 = wildcard

        chunks: list[dict[str, list[str]]] = []
        names = list(wildcard)
        for i in range(0, len(names), chunk_size):
            chunks.append({n: ["*"] for n in names[i : i + chunk_size]})
        if enumerated:
            chunks.append(enumerated)
        chunks.append({t: ["*"] for t in HEAVY_TYPES})

        # --- retrieve chunk by chunk, dropping types the org rejects ----------
        out_root = workspace / "mdapi"
        for idx, chunk in enumerate(chunks):
            tick(f"retrieving chunk {idx + 1} ({', '.join(list(chunk)[:3])}...)",
                 idx, len(chunks))
            remaining = dict(chunk)
            attempt = 0
            # An unknown or unsupported type name fails the WHOLE manifest, so a
            # single bad name would cost every other type sharing the chunk.
            # Parse the offending name out of the error and retry without it -
            # the same self-healing approach used for unaggregatable fields.
            while remaining and attempt <= len(chunk):
                manifest = build_manifest(
                    workspace / "manifest" / f"package-{idx:02d}.xml",
                    remaining, conn.api_version)
                if manifest not in result.manifests:
                    result.manifests.append(manifest)
                ok, detail = await cli.retrieve(manifest, out_root / f"chunk-{idx:02d}")
                if ok:
                    log.info("retrieve_chunk_ok", chunk=idx, types=len(remaining))
                    break
                bad = _offending_type(detail, list(remaining))
                if not bad:
                    for t in remaining:
                        result.failed_types[t] = detail[:300]
                    log.warning("retrieve_chunk_failed", chunk=idx,
                                types=list(remaining), detail=detail[:200])
                    break
                result.failed_types[bad] = _short_reason(detail)
                remaining.pop(bad, None)
                attempt += 1
                log.info("retrieve_dropped_type", chunk=idx, type=bad,
                         remaining=len(remaining))

        result.files_retrieved, result.bytes_retrieved = _extract_all(out_root)
        _reconcile_folder_types(result, out_root)
    finally:
        await cli.logout()

    log.info("retrieve_complete", files=result.files_retrieved,
             bytes=result.bytes_retrieved, failed=len(result.failed_types))
    return result


def _reconcile_folder_types(result: "RetrieveResult", out_root) -> None:
    """Compare folder-scoped members requested against files actually received.

    Salesforce can accept a manifest naming members it then declines to return
    -- managed Enablement dashboards do exactly this. Without a comparison the
    omission is invisible, and every component referenced only by the missing
    metadata quietly becomes a deletion candidate.
    """
    for mdtype, dirname in FOLDER_SCOPED_DIRS.items():
        asked = result.types_requested.get(mdtype, 0)
        if asked <= 0:            # not requested, or a wildcard type
            continue
        got = 0
        for chunk in out_root.glob("chunk-*/extracted/unpackaged"):
            d = chunk / dirname
            if d.is_dir():
                got += sum(1 for f in d.rglob("*") if f.is_file())
        if got == 0:
            result.warnings.append(
                f"{mdtype}: {asked} requested, 0 returned by Salesforce. "
                f"COVERAGE GAP - components referenced only by {mdtype.lower()}s "
                "cannot be seen, so any such component may look unused."
            )
            log.warning("retrieve_type_empty", type=mdtype, requested=asked)


def _offending_type(error_text: str, candidates: list[str]) -> str | None:
    """Pull the rejected metadata type out of a retrieve error.

    Matched against the requested types rather than by regex on the message, so
    it survives wording changes between CLI versions.
    """
    low = error_text.lower()
    if not any(k in low for k in ("registryerror", "unknown", "not supported",
                                  "missing metadata type", "invalid type")):
        return None
    for t in sorted(candidates, key=len, reverse=True):
        if t.lower() in low:
            return t
    return None


def _short_reason(detail: str) -> str:
    try:
        payload = json.loads(detail)
        return str(payload.get("message", detail))[:240]
    except Exception:
        return detail[:240]


def _extract_all(out_root: Path) -> tuple[int, int]:
    """Unzip every ``unpackaged.zip`` so the indexer sees real files.

    ``--target-metadata-dir`` writes a zip per chunk rather than a source tree,
    which is easy to miss: the retrieve reports success and the workspace looks
    almost empty.
    """
    import zipfile

    files = total_bytes = 0
    if not out_root.exists():
        return 0, 0
    for zpath in out_root.rglob("*.zip"):
        dest = zpath.parent / "extracted"
        try:
            with zipfile.ZipFile(zpath) as z:
                z.extractall(dest)
        except zipfile.BadZipFile:
            log.warning("bad_zip", path=str(zpath))
            continue
    for p in out_root.rglob("*"):
        if p.is_file() and p.suffix.lower() != ".zip":
            files += 1
            total_bytes += p.stat().st_size
    return files, total_bytes


def clean_workspace(workspace: Path) -> None:
    if workspace.exists():
        shutil.rmtree(workspace, ignore_errors=True)
