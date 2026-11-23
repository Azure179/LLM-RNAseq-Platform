"""One Registry per application lifespan, shared by metadata and execution endpoints."""

from fastapi import Request

from bulk_rnaseq.registry import SkillRegistry
from bulk_rnaseq.workspace.skill_execution import SingleSkillService
from bulk_rnaseq.workspace.workflow_service import WorkflowService
from bulk_rnaseq.workspace.assistant_service import AssistantService
from bulk_rnaseq.workspace.customization_service import CustomizationService


def get_customization_service(request: Request) -> CustomizationService:
    return request.app.state.customization_service


def get_assistant_service(request: Request) -> AssistantService:
    return request.app.state.assistant_service


def get_skill_registry(request: Request) -> SkillRegistry:
    return request.app.state.skill_registry


def get_execution_service(request: Request) -> SingleSkillService:
    return request.app.state.execution_service


def get_workflow_service(request: Request) -> WorkflowService:
    return request.app.state.workflow_service
