"""GrovePackage import / export service.

Idempotent apply of a GrovePackage YAML against the Grove DB plus Sinas
(for playbook skill content). Rows installed by a package are tagged
`managed_by = pkg:<name>` so a re-import can prune resources that were
removed from the manifest.

Order of operations on apply:
  1. Parse + structural validate the YAML.
  2. Cross-ref validate (relationships reference existing names, etc.).
  3. Push playbook skills to Sinas first (errors abort before any Grove
     DB write — the Grove transaction never starts if Sinas is unreachable).
  4. Single Grove DB transaction:
       entity_types → document_classes (+ properties + entity-type links)
       → dossier_classes (+ properties + document-class links)
       → relationship_definitions (+ states)
       → playbook_scope rows.
  5. If prune=True, delete managed_by-tagged rows / Sinas skills not in
     the manifest.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

import yaml
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    DocumentClass,
    DocumentClassEntityType,
    DocumentClassProperty,
    DossierClass,
    DossierClassDocumentClass,
    DossierClassProperty,
    EntityType,
    PlaybookScope,
    RelationshipDefinition,
    RelationshipState,
)
from app.schemas.config import slugify
from app.schemas.package import (
    GrovePackage,
    PackageDiff,
    PackageImportResult,
    PackagePlaybookEntry,
    PackageValidateResult,
)
from app.services.sinas import Management

PLAYBOOK_NAMESPACE_FOR = {
    "retrieval": "grove_retrieval_playbooks",
    "synthesis": "grove_synthesis_playbooks",
}


# ─────────────────────────────────────────────────────────────
# Parsing
# ─────────────────────────────────────────────────────────────
def parse_package(yaml_text: str) -> tuple[GrovePackage | None, list[str]]:
    """Parse YAML into a GrovePackage; return (package, errors)."""
    try:
        raw = yaml.safe_load(yaml_text)
    except yaml.YAMLError as exc:
        return None, [f"YAML parse error: {exc}"]
    if not isinstance(raw, dict):
        return None, ["YAML root must be a mapping"]
    try:
        return GrovePackage.model_validate(raw), []
    except ValidationError as exc:
        return None, [str(e) for e in exc.errors()]


def _tag(pkg: GrovePackage) -> str:
    return f"pkg:{pkg.package.name}"


# ─────────────────────────────────────────────────────────────
# Cross-reference validation
# ─────────────────────────────────────────────────────────────
def validate_crossrefs(pkg: GrovePackage) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []

    dc_names = {dc.name for dc in pkg.spec.document_classes}
    et_names = {et.name for et in pkg.spec.entity_types}
    doss_names = {d.name for d in pkg.spec.dossier_classes}

    for dc in pkg.spec.document_classes:
        for et_name in dc.entity_types:
            if et_name not in et_names:
                errors.append(
                    f"document class '{dc.name}' references entity type '{et_name}' which is not defined in the package"
                )

    for dossier in pkg.spec.dossier_classes:
        for link in dossier.document_classes:
            if link.document_class not in dc_names:
                errors.append(
                    f"dossier class '{dossier.name}' links to document class '{link.document_class}' which is not defined in the package"
                )

    for rdef in pkg.spec.relationship_definitions:
        for side, ref in (("source", rdef.source), ("target", rdef.target)):
            valid = {
                "document_class": dc_names,
                "entity_type": et_names,
                "dossier_class": doss_names,
            }[ref.type]
            if ref.name not in valid:
                errors.append(
                    f"relationship '{rdef.name}' {side} references {ref.type} '{ref.name}' which is not defined in the package"
                )

    for pb in pkg.spec.playbooks:
        for s in pb.scope:
            if s.document_class and s.document_class not in dc_names:
                errors.append(
                    f"playbook '{pb.name}' scope references document class '{s.document_class}' which is not defined in the package"
                )
            if s.dossier_class and s.dossier_class not in doss_names:
                errors.append(
                    f"playbook '{pb.name}' scope references dossier class '{s.dossier_class}' which is not defined in the package"
                )

    return errors, warnings


def validate(yaml_text: str) -> PackageValidateResult:
    pkg, parse_errors = parse_package(yaml_text)
    if parse_errors:
        return PackageValidateResult(valid=False, errors=parse_errors)
    assert pkg is not None
    errors, warnings = validate_crossrefs(pkg)
    return PackageValidateResult(valid=not errors, errors=errors, warnings=warnings)


# ─────────────────────────────────────────────────────────────
# Apply
# ─────────────────────────────────────────────────────────────
@dataclass
class _ApplyCtx:
    pkg: GrovePackage
    tag: str
    session: AsyncSession
    mgmt: Management
    sinas_token: str
    prune: bool
    diff: PackageDiff = field(default_factory=PackageDiff)
    warnings: list[str] = field(default_factory=list)


async def apply(
    yaml_text: str,
    session: AsyncSession,
    mgmt: Management,
    sinas_token: str,
    prune: bool = True,
) -> PackageImportResult:
    pkg, parse_errors = parse_package(yaml_text)
    if parse_errors:
        raise ValueError("Package parse failed: " + "; ".join(parse_errors))
    assert pkg is not None
    errors, warnings = validate_crossrefs(pkg)
    if errors:
        raise ValueError("Package validation failed: " + "; ".join(errors))

    ctx = _ApplyCtx(
        pkg=pkg,
        tag=_tag(pkg),
        session=session,
        mgmt=mgmt,
        sinas_token=sinas_token,
        prune=prune,
        warnings=list(warnings),
    )

    # 1. Sinas-side writes first — if Sinas is unreachable, Grove stays untouched.
    await _push_playbook_skills(ctx)

    # 2. Grove DB writes in dependency order.
    await _apply_entity_types(ctx)
    await _apply_document_classes(ctx)
    await _apply_dossier_classes(ctx)
    await _apply_relationship_definitions(ctx)
    await _apply_playbook_scopes(ctx)

    # 3. Prune resources from prior installs not in the manifest.
    if prune:
        await _prune(ctx)

    await session.commit()

    return PackageImportResult(
        package=pkg.package.name,
        version=pkg.package.version,
        diff=ctx.diff,
        warnings=ctx.warnings,
    )


# ────────── helpers: upsert by (managed_by, name) ──────────
async def _existing_managed(
    ctx: _ApplyCtx, model: Any, name_col: str = "name"
) -> dict[str, Any]:
    rows = (
        await ctx.session.execute(
            select(model).where(model.managed_by == ctx.tag)
        )
    ).scalars().all()
    return {getattr(r, name_col): r for r in rows}


def _track(diff: PackageDiff, kind: str, name: str, created: bool, changed: bool) -> None:
    label = f"{kind}/{name}"
    if created:
        diff.created.append(label)
    elif changed:
        diff.updated.append(label)
    else:
        diff.unchanged.append(label)


def _assign_if_changed(row: Any, fields: dict[str, Any]) -> bool:
    changed = False
    for k, v in fields.items():
        if getattr(row, k) != v:
            setattr(row, k, v)
            changed = True
    return changed


# ────────── entity types ──────────
async def _apply_entity_types(ctx: _ApplyCtx) -> None:
    existing = await _existing_managed(ctx, EntityType)
    seen: set[str] = set()
    for et in ctx.pkg.spec.entity_types:
        seen.add(et.name)
        row = existing.get(et.name)
        if row is None:
            # An unmanaged row with the same name? Take it over.
            row = (
                await ctx.session.execute(
                    select(EntityType).where(EntityType.name == et.name)
                )
            ).scalar_one_or_none()
        created = row is None
        if row is None:
            row = EntityType(name=et.name)
            ctx.session.add(row)
        changed = _assign_if_changed(
            row,
            {
                "description": et.description,
                "guidance": et.guidance,
                "creation_mode": et.creation_mode,
                "managed_by": ctx.tag,
            },
        )
        await ctx.session.flush()
        _track(ctx.diff, "entity_type", et.name, created, changed)


# ────────── document classes ──────────
async def _resolve_entity_types_by_name(
    ctx: _ApplyCtx, names: Iterable[str]
) -> dict[str, EntityType]:
    name_list = list(names)
    if not name_list:
        return {}
    rows = (
        await ctx.session.execute(
            select(EntityType).where(EntityType.name.in_(name_list))
        )
    ).scalars().all()
    return {r.name: r for r in rows}


async def _apply_document_classes(ctx: _ApplyCtx) -> None:
    existing = await _existing_managed(ctx, DocumentClass)
    for dc in ctx.pkg.spec.document_classes:
        row = existing.get(dc.name) or (
            await ctx.session.execute(
                select(DocumentClass).where(DocumentClass.name == dc.name)
            )
        ).scalar_one_or_none()
        created = row is None
        if row is None:
            row = DocumentClass(name=dc.name, slug=dc.slug or slugify(dc.name))
            ctx.session.add(row)
            await ctx.session.flush()
        changed = _assign_if_changed(
            row,
            {
                "description": dc.description,
                "summarization_guidance": dc.summarization_guidance,
                "classification_hints": dc.classification_hints,
                "managed_by": ctx.tag,
            },
        )

        # Reconcile properties (delete-then-add by name).
        prop_rows = (
            await ctx.session.execute(
                select(DocumentClassProperty).where(
                    DocumentClassProperty.document_class_id == row.id
                )
            )
        ).scalars().all()
        by_name = {p.name: p for p in prop_rows}
        manifest_names = {p.name for p in dc.properties}
        for old in prop_rows:
            if old.name not in manifest_names:
                await ctx.session.delete(old)
                changed = True
        for prop in dc.properties:
            p_row = by_name.get(prop.name)
            if p_row is None:
                p_row = DocumentClassProperty(document_class_id=row.id, name=prop.name)
                ctx.session.add(p_row)
                changed = True
            p_changed = _assign_if_changed(
                p_row,
                {
                    "description": prop.description,
                    "schema": prop.schema,
                    "guidance": prop.guidance,
                    "manual": prop.manual,
                    "required": prop.required,
                    "cardinality": prop.cardinality,
                    "schema_version": prop.schema_version,
                },
            )
            changed = changed or p_changed

        # Reconcile entity_type attachments.
        et_map = await _resolve_entity_types_by_name(ctx, dc.entity_types)
        existing_links = (
            await ctx.session.execute(
                select(DocumentClassEntityType).where(
                    DocumentClassEntityType.document_class_id == row.id
                )
            )
        ).scalars().all()
        wanted_ids = {et.id for et in et_map.values()}
        for link in existing_links:
            if link.entity_type_id not in wanted_ids:
                await ctx.session.delete(link)
                changed = True
        present_ids = {link.entity_type_id for link in existing_links}
        for et in et_map.values():
            if et.id not in present_ids:
                ctx.session.add(
                    DocumentClassEntityType(
                        document_class_id=row.id, entity_type_id=et.id
                    )
                )
                changed = True

        await ctx.session.flush()
        _track(ctx.diff, "document_class", dc.name, created, changed)


# ────────── dossier classes ──────────
async def _apply_dossier_classes(ctx: _ApplyCtx) -> None:
    existing = await _existing_managed(ctx, DossierClass)
    # Build name→DocumentClass map for link resolution.
    all_dc = (
        await ctx.session.execute(select(DocumentClass))
    ).scalars().all()
    dc_by_name = {dc.name: dc for dc in all_dc}

    for dossier in ctx.pkg.spec.dossier_classes:
        row = existing.get(dossier.name) or (
            await ctx.session.execute(
                select(DossierClass).where(DossierClass.name == dossier.name)
            )
        ).scalar_one_or_none()
        created = row is None
        if row is None:
            row = DossierClass(
                name=dossier.name, slug=dossier.slug or slugify(dossier.name)
            )
            ctx.session.add(row)
            await ctx.session.flush()
        changed = _assign_if_changed(
            row,
            {
                "description": dossier.description,
                "guidance": dossier.guidance,
                "summarization_guidance": dossier.summarization_guidance,
                "classification_hints": dossier.classification_hints,
                "managed_by": ctx.tag,
            },
        )

        # Properties.
        prop_rows = (
            await ctx.session.execute(
                select(DossierClassProperty).where(
                    DossierClassProperty.dossier_class_id == row.id
                )
            )
        ).scalars().all()
        by_name = {p.name: p for p in prop_rows}
        manifest_names = {p.name for p in dossier.properties}
        for old in prop_rows:
            if old.name not in manifest_names:
                await ctx.session.delete(old)
                changed = True
        for prop in dossier.properties:
            p_row = by_name.get(prop.name)
            if p_row is None:
                p_row = DossierClassProperty(dossier_class_id=row.id, name=prop.name)
                ctx.session.add(p_row)
                changed = True
            p_changed = _assign_if_changed(
                p_row,
                {
                    "description": prop.description,
                    "schema": prop.schema,
                    "guidance": prop.guidance,
                    "manual": prop.manual,
                    "required": prop.required,
                    "cardinality": prop.cardinality,
                    "schema_version": prop.schema_version,
                },
            )
            changed = changed or p_changed

        # Document-class links.
        existing_links = (
            await ctx.session.execute(
                select(DossierClassDocumentClass).where(
                    DossierClassDocumentClass.dossier_class_id == row.id
                )
            )
        ).scalars().all()
        wanted = {
            dc_by_name[link.document_class].id: link
            for link in dossier.document_classes
            if link.document_class in dc_by_name
        }
        by_dc_id = {link.document_class_id: link for link in existing_links}
        for dc_id, existing_link in by_dc_id.items():
            if dc_id not in wanted:
                await ctx.session.delete(existing_link)
                changed = True
        for dc_id, manifest_link in wanted.items():
            link_row = by_dc_id.get(dc_id)
            if link_row is None:
                ctx.session.add(
                    DossierClassDocumentClass(
                        dossier_class_id=row.id,
                        document_class_id=dc_id,
                        required=manifest_link.required,
                        cardinality=manifest_link.cardinality,
                    )
                )
                changed = True
            else:
                l_changed = _assign_if_changed(
                    link_row,
                    {
                        "required": manifest_link.required,
                        "cardinality": manifest_link.cardinality,
                    },
                )
                changed = changed or l_changed

        await ctx.session.flush()
        _track(ctx.diff, "dossier_class", dossier.name, created, changed)


# ────────── relationship definitions ──────────
async def _apply_relationship_definitions(ctx: _ApplyCtx) -> None:
    existing = await _existing_managed(ctx, RelationshipDefinition)

    dc_by_name = {
        dc.name: dc
        for dc in (await ctx.session.execute(select(DocumentClass))).scalars().all()
    }
    et_by_name = {
        et.name: et
        for et in (await ctx.session.execute(select(EntityType))).scalars().all()
    }
    doss_by_name = {
        d.name: d
        for d in (await ctx.session.execute(select(DossierClass))).scalars().all()
    }

    def _resolve(ref_type: str, name: str):
        if ref_type == "document_class":
            return dc_by_name[name]
        if ref_type == "entity_type":
            return et_by_name[name]
        return doss_by_name[name]

    for rdef in ctx.pkg.spec.relationship_definitions:
        row = existing.get(rdef.name) or (
            await ctx.session.execute(
                select(RelationshipDefinition).where(
                    RelationshipDefinition.name == rdef.name
                )
            )
        ).scalar_one_or_none()
        created = row is None
        source = _resolve(rdef.source.type, rdef.source.name)
        target = _resolve(rdef.target.type, rdef.target.name)
        if row is None:
            row = RelationshipDefinition(
                name=rdef.name,
                source_ref_type=rdef.source.type,
                source_ref_id=source.id,
                target_ref_type=rdef.target.type,
                target_ref_id=target.id,
            )
            ctx.session.add(row)
            await ctx.session.flush()
        changed = _assign_if_changed(
            row,
            {
                "description": rdef.description,
                "source_ref_type": rdef.source.type,
                "source_ref_id": source.id,
                "target_ref_type": rdef.target.type,
                "target_ref_id": target.id,
                "cardinality": rdef.cardinality,
                "extraction_guidance": rdef.extraction_guidance,
                "discovery_guidance": rdef.discovery_guidance,
                "creation_mode": rdef.creation_mode,
                "managed_by": ctx.tag,
            },
        )

        state_rows = (
            await ctx.session.execute(
                select(RelationshipState).where(
                    RelationshipState.relationship_definition_id == row.id
                )
            )
        ).scalars().all()
        by_name = {s.name: s for s in state_rows}
        manifest_names = {s.name for s in rdef.states}
        for old in state_rows:
            if old.name not in manifest_names:
                await ctx.session.delete(old)
                changed = True
        for state in rdef.states:
            s_row = by_name.get(state.name)
            if s_row is None:
                s_row = RelationshipState(
                    relationship_definition_id=row.id, name=state.name
                )
                ctx.session.add(s_row)
                changed = True
            s_changed = _assign_if_changed(
                s_row,
                {
                    "description": state.description,
                    "counts_as_active": state.counts_as_active,
                },
            )
            changed = changed or s_changed

        await ctx.session.flush()
        _track(ctx.diff, "relationship_definition", rdef.name, created, changed)


# ────────── playbooks ──────────
async def _push_playbook_skills(ctx: _ApplyCtx) -> None:
    for pb in ctx.pkg.spec.playbooks:
        ns = PLAYBOOK_NAMESPACE_FOR[pb.kind]
        await ctx.mgmt.upsert_skill(
            ctx.sinas_token, ns, pb.name, pb.description, pb.content
        )


async def _apply_playbook_scopes(ctx: _ApplyCtx) -> None:
    # Resolve target classes by name once.
    dc_by_name = {
        dc.name: dc
        for dc in (await ctx.session.execute(select(DocumentClass))).scalars().all()
    }
    doss_by_name = {
        d.name: d
        for d in (await ctx.session.execute(select(DossierClass))).scalars().all()
    }

    # Wipe existing managed scope rows for this package's playbooks, then re-add
    # from the manifest. Cleaner than a per-row diff given the composite key.
    pb_keys = {(PLAYBOOK_NAMESPACE_FOR[pb.kind], pb.name) for pb in ctx.pkg.spec.playbooks}
    if pb_keys:
        rows = (
            await ctx.session.execute(
                select(PlaybookScope).where(PlaybookScope.managed_by == ctx.tag)
            )
        ).scalars().all()
        for r in rows:
            if (r.skill_namespace, r.skill_name) in pb_keys:
                await ctx.session.delete(r)
        await ctx.session.flush()

    for pb in ctx.pkg.spec.playbooks:
        ns = PLAYBOOK_NAMESPACE_FOR[pb.kind]
        scope_entries = pb.scope or [None]  # sentinel for "applies everywhere"
        for s in scope_entries:
            dc_id = None
            doss_id = None
            if s is not None:
                if s.document_class:
                    dc_id = dc_by_name[s.document_class].id
                if s.dossier_class:
                    doss_id = doss_by_name[s.dossier_class].id
            ctx.session.add(
                PlaybookScope(
                    skill_namespace=ns,
                    skill_name=pb.name,
                    document_class_id=dc_id,
                    dossier_class_id=doss_id,
                    managed_by=ctx.tag,
                )
            )
        _track(ctx.diff, f"playbook/{pb.kind}", pb.name, True, False)
    await ctx.session.flush()


# ────────── prune ──────────
async def _prune(ctx: _ApplyCtx) -> None:
    manifest_names = {
        "entity_type": {et.name for et in ctx.pkg.spec.entity_types},
        "document_class": {dc.name for dc in ctx.pkg.spec.document_classes},
        "dossier_class": {d.name for d in ctx.pkg.spec.dossier_classes},
        "relationship_definition": {r.name for r in ctx.pkg.spec.relationship_definitions},
    }
    model_for = {
        "entity_type": EntityType,
        "document_class": DocumentClass,
        "dossier_class": DossierClass,
        "relationship_definition": RelationshipDefinition,
    }

    for kind, model in model_for.items():
        rows = (
            await ctx.session.execute(
                select(model).where(model.managed_by == ctx.tag)
            )
        ).scalars().all()
        wanted = manifest_names[kind]
        for r in rows:
            if r.name not in wanted:
                await ctx.session.delete(r)
                ctx.diff.deleted.append(f"{kind}/{r.name}")

    # Playbooks: any (namespace, name) tagged for this package and NOT in the
    # manifest is deleted from Sinas + from Grove scope rows.
    manifest_pb = {
        (PLAYBOOK_NAMESPACE_FOR[pb.kind], pb.name) for pb in ctx.pkg.spec.playbooks
    }
    rows = (
        await ctx.session.execute(
            select(PlaybookScope).where(PlaybookScope.managed_by == ctx.tag)
        )
    ).scalars().all()
    tagged_pb = {(r.skill_namespace, r.skill_name) for r in rows}
    stale = tagged_pb - manifest_pb
    for r in rows:
        if (r.skill_namespace, r.skill_name) in stale:
            await ctx.session.delete(r)
    for ns, name in stale:
        try:
            await ctx.mgmt.delete_skill(ctx.sinas_token, ns, name)
            ctx.diff.deleted.append(f"playbook/{ns}/{name}")
        except Exception as exc:  # noqa: BLE001
            ctx.warnings.append(
                f"failed to delete stale skill {ns}/{name} from Sinas: {exc}"
            )

    await ctx.session.flush()


# ─────────────────────────────────────────────────────────────
# Export
# ─────────────────────────────────────────────────────────────
async def export_package(
    name: str,
    session: AsyncSession,
    mgmt: Management,
    sinas_token: str,
    version: str = "0.0.0",
) -> str:
    """Export all rows tagged `pkg:<name>` (plus their nested resources) as YAML."""
    tag = f"pkg:{name}"

    et_rows = (
        await session.execute(
            select(EntityType).where(EntityType.managed_by == tag)
        )
    ).scalars().all()
    dc_rows = (
        await session.execute(
            select(DocumentClass).where(DocumentClass.managed_by == tag)
        )
    ).scalars().all()
    doss_rows = (
        await session.execute(
            select(DossierClass).where(DossierClass.managed_by == tag)
        )
    ).scalars().all()
    rdef_rows = (
        await session.execute(
            select(RelationshipDefinition).where(RelationshipDefinition.managed_by == tag)
        )
    ).scalars().all()

    # Lookup tables for ref resolution on export.
    et_by_id = {et.id: et for et in (await session.execute(select(EntityType))).scalars().all()}
    dc_by_id = {dc.id: dc for dc in (await session.execute(select(DocumentClass))).scalars().all()}
    doss_by_id = {d.id: d for d in (await session.execute(select(DossierClass))).scalars().all()}

    def _ref_name(ref_type: str, ref_id) -> str:
        if ref_type == "document_class":
            return dc_by_id[ref_id].name
        if ref_type == "entity_type":
            return et_by_id[ref_id].name
        return doss_by_id[ref_id].name

    spec: dict[str, Any] = {}

    if et_rows:
        spec["entity_types"] = [
            {
                "name": et.name,
                "description": et.description,
                "guidance": et.guidance,
                "creation_mode": et.creation_mode,
            }
            for et in et_rows
        ]

    if dc_rows:
        out_dc = []
        for dc in dc_rows:
            props = (
                await session.execute(
                    select(DocumentClassProperty).where(
                        DocumentClassProperty.document_class_id == dc.id
                    )
                )
            ).scalars().all()
            links = (
                await session.execute(
                    select(EntityType)
                    .join(
                        DocumentClassEntityType,
                        DocumentClassEntityType.entity_type_id == EntityType.id,
                    )
                    .where(DocumentClassEntityType.document_class_id == dc.id)
                )
            ).scalars().all()
            out_dc.append(
                {
                    "name": dc.name,
                    "slug": dc.slug,
                    "description": dc.description,
                    "summarization_guidance": dc.summarization_guidance,
                    "classification_hints": dc.classification_hints,
                    "properties": [_export_property(p) for p in props],
                    "entity_types": [et.name for et in links],
                }
            )
        spec["document_classes"] = out_dc

    if doss_rows:
        out_doss = []
        for d in doss_rows:
            props = (
                await session.execute(
                    select(DossierClassProperty).where(
                        DossierClassProperty.dossier_class_id == d.id
                    )
                )
            ).scalars().all()
            links = (
                await session.execute(
                    select(DossierClassDocumentClass).where(
                        DossierClassDocumentClass.dossier_class_id == d.id
                    )
                )
            ).scalars().all()
            out_doss.append(
                {
                    "name": d.name,
                    "slug": d.slug,
                    "description": d.description,
                    "guidance": d.guidance,
                    "summarization_guidance": d.summarization_guidance,
                    "classification_hints": d.classification_hints,
                    "properties": [_export_property(p) for p in props],
                    "document_classes": [
                        {
                            "document_class": dc_by_id[link.document_class_id].name,
                            "required": link.required,
                            "cardinality": link.cardinality,
                        }
                        for link in links
                    ],
                }
            )
        spec["dossier_classes"] = out_doss

    if rdef_rows:
        out_rdef = []
        for r in rdef_rows:
            states = (
                await session.execute(
                    select(RelationshipState).where(
                        RelationshipState.relationship_definition_id == r.id
                    )
                )
            ).scalars().all()
            out_rdef.append(
                {
                    "name": r.name,
                    "description": r.description,
                    "source": {
                        "type": r.source_ref_type,
                        "name": _ref_name(r.source_ref_type, r.source_ref_id),
                    },
                    "target": {
                        "type": r.target_ref_type,
                        "name": _ref_name(r.target_ref_type, r.target_ref_id),
                    },
                    "cardinality": r.cardinality,
                    "extraction_guidance": r.extraction_guidance,
                    "discovery_guidance": r.discovery_guidance,
                    "creation_mode": r.creation_mode,
                    "states": [
                        {
                            "name": s.name,
                            "description": s.description,
                            "counts_as_active": s.counts_as_active,
                        }
                        for s in states
                    ],
                }
            )
        spec["relationship_definitions"] = out_rdef

    # Playbooks: managed scope rows tell us which skills belong to the package.
    pb_rows = (
        await session.execute(
            select(PlaybookScope).where(PlaybookScope.managed_by == tag)
        )
    ).scalars().all()
    by_skill: dict[tuple[str, str], list[PlaybookScope]] = {}
    for row in pb_rows:
        by_skill.setdefault((row.skill_namespace, row.skill_name), []).append(row)
    if by_skill:
        out_pb = []
        kind_for_ns = {v: k for k, v in PLAYBOOK_NAMESPACE_FOR.items()}
        for (ns, sk_name), scope_rows in by_skill.items():
            if ns not in kind_for_ns:
                continue
            skill = await mgmt.get_skill(sinas_token, ns, sk_name)
            content = (skill or {}).get("content", "")
            description = (skill or {}).get("description", "")
            scope_out: list[dict[str, str]] = []
            for s in scope_rows:
                if s.document_class_id is None and s.dossier_class_id is None:
                    continue  # sentinel = applies everywhere
                entry: dict[str, str] = {}
                if s.document_class_id:
                    entry["document_class"] = dc_by_id[s.document_class_id].name
                if s.dossier_class_id:
                    entry["dossier_class"] = doss_by_id[s.dossier_class_id].name
                scope_out.append(entry)
            out_pb.append(
                {
                    "kind": kind_for_ns[ns],
                    "name": sk_name,
                    "description": description,
                    "content": content,
                    "scope": scope_out,
                }
            )
        spec["playbooks"] = out_pb

    doc = {
        "apiVersion": "grove.sinas.co/v1",
        "kind": "GrovePackage",
        "metadata": {"name": name},
        "package": {"name": name, "version": version},
        "spec": spec,
    }
    return yaml.safe_dump(doc, sort_keys=False, allow_unicode=True)


def _export_property(p: Any) -> dict[str, Any]:
    return {
        "name": p.name,
        "description": p.description,
        "schema": p.schema,
        "guidance": p.guidance,
        "manual": p.manual,
        "required": p.required,
        "cardinality": p.cardinality,
        "schema_version": p.schema_version,
    }
