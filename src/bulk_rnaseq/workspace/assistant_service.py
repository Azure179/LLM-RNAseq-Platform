"""Natural language facade over the existing persisted Chat controller."""

from uuid import uuid4

from bulk_rnaseq.llm.client import DeepSeekClient
from bulk_rnaseq.llm.config import load_deepseek_config
from bulk_rnaseq.llm.single_request import SingleRequestRunner
from bulk_rnaseq.workspace.chat_controller import ChatController, ChatResult
from bulk_rnaseq.workspace.skill_execution import SingleSkillService
from bulk_rnaseq.workspace.workflow_service import WorkflowService


class AssistantService:
    def __init__(self, single: SingleSkillService, workflow: WorkflowService) -> None:
        self.single, self.workflow = single, workflow
        self.manager = single.manager

    def create_client(self) -> DeepSeekClient:
        return DeepSeekClient(load_deepseek_config(self.workflow.env_file))

    def analyze(self, project_id: str, text: str, *, request_id: str | None = None,
                source_run_id: str | None = None) -> ChatResult:
        self.manager.open_project(project_id)  # 404 before any paid request.

        def runner() -> SingleRequestRunner:
            return SingleRequestRunner(self.create_client(), self.single.registry,
                execute_business=lambda name, arguments: self.single.execute(name, project_id, arguments).result)

        controller = ChatController(self.manager, self.workflow.env_file, runner_factory=runner,
                                    workflow_service=self.workflow)
        return controller.submit(project_id, text, request_id or str(uuid4()), source_run_id=source_run_id)
