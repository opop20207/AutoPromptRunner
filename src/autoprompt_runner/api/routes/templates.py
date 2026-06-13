"""Template routes. Thin handlers over the templates module and storage layer."""

from __future__ import annotations

from typing import Dict, List

from fastapi import APIRouter, Depends, HTTPException

from ... import templates
from ...models import Template
from ..dependencies import get_db_path
from ..schemas import (
    TemplateCreateRequest,
    TemplateRenderRequest,
    TemplateRenderResponse,
    TemplateResponse,
    TemplateSeedResponse,
)

router = APIRouter(prefix="/templates", tags=["templates"])


def _to_response(template: Template) -> TemplateResponse:
    return TemplateResponse(
        id=template.id,
        name=template.name,
        description=template.description,
        body=template.body,
        tags=template.tags,
        created_at=template.created_at,
        updated_at=template.updated_at,
    )


@router.post("/seed", response_model=TemplateSeedResponse)
def seed_templates(db_path: str = Depends(get_db_path)) -> TemplateSeedResponse:
    result = templates.seed_templates(db_path)
    return TemplateSeedResponse(**result)


@router.get("", response_model=List[TemplateResponse])
def list_templates(db_path: str = Depends(get_db_path)) -> List[TemplateResponse]:
    return [_to_response(template) for template in templates.list_templates(db_path)]


@router.post("", response_model=TemplateResponse, status_code=201)
def create_template(body: TemplateCreateRequest, db_path: str = Depends(get_db_path)) -> TemplateResponse:
    name = (body.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name must not be empty")
    if not (body.body or "").strip():
        raise HTTPException(status_code=400, detail="body must not be empty")
    if templates.get_template_by_name(db_path, name) is not None:
        raise HTTPException(status_code=400, detail=f"template '{name}' already exists")
    template_id = templates.create_template(
        db_path, name=name, body=body.body, description=body.description or "", tags=body.tags or []
    )
    return _to_response(templates.get_template_by_id(db_path, template_id))


@router.get("/{template_name}", response_model=TemplateResponse)
def get_template(template_name: str, db_path: str = Depends(get_db_path)) -> TemplateResponse:
    template = templates.get_template_by_name(db_path, template_name)
    if template is None:
        raise HTTPException(status_code=404, detail=f"template '{template_name}' not found")
    return _to_response(template)


@router.delete("/{template_name}")
def delete_template(template_name: str, db_path: str = Depends(get_db_path)) -> Dict[str, object]:
    template = templates.get_template_by_name(db_path, template_name)
    if template is None:
        raise HTTPException(status_code=404, detail=f"template '{template_name}' not found")
    templates.delete_template(db_path, template.id)
    return {"deleted": template.name}


@router.post("/{template_name}/render", response_model=TemplateRenderResponse)
def render_template(
    template_name: str,
    body: TemplateRenderRequest,
    db_path: str = Depends(get_db_path),
) -> TemplateRenderResponse:
    template = templates.get_template_by_name(db_path, template_name)
    if template is None:
        raise HTTPException(status_code=404, detail=f"template '{template_name}' not found")
    values = templates.build_render_values(
        project_name=body.project_name,
        workspace=body.workspace,
        goal=body.goal,
        changed_files=body.changed_files,
        last_error=body.last_error,
        extra_context=body.extra_context,
    )
    return TemplateRenderResponse(name=template.name, rendered=templates.render_template(template.body, values))
