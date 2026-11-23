"""Closed smoke-only protocol. Host authorization remains separate from validation."""
from typing import Literal, Self
from uuid import uuid4
from pydantic import BaseModel, ConfigDict, Field, model_validator, field_validator
from bulk_rnaseq.workspace.models import validate_name


class Closed(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True)


class Parameters(Closed):
    pass


class Source(Closed):
    run_id: str = Field(pattern=r'^[A-Za-z0-9_][A-Za-z0-9_.-]{0,159}$')
    artifact_id: Literal['deg_csv'] = 'deg_csv'


class ExecutionRequest(Closed):
    execution_id: str = Field(default_factory=lambda: uuid4().hex, pattern=r'^[a-f0-9]{32}$')
    project_id: str
    source_result_ref: Source
    approved_task_type: Literal['smoke', 'test_timeout', 'test_oom', 'test_failure', 'test_bad_output'] = 'smoke'
    validated_parameters: Parameters = Field(default_factory=Parameters)
    input_artifact_refs: tuple[Literal['source'], ...] = ('source',)
    output_expectations: Literal['receipt-v1'] = 'receipt-v1'
    resource_profile: Literal['smoke-small'] = 'smoke-small'
    execution_status: Literal['CREATED'] = 'CREATED'

    @field_validator('project_id')
    @classmethod
    def project(cls, value: str) -> str:
        return validate_name(value)

    @model_validator(mode='after')
    def inputs(self) -> Self:
        if self.input_artifact_refs != ('source',):
            raise ValueError('Exactly one approved source artifact required')
        return self


TRANSITIONS = {'CREATED': {'VALIDATED', 'FAILED'}, 'VALIDATED': {'RUNNING', 'FAILED', 'CANCELLED'},
               'RUNNING': {'SUCCEEDED', 'FAILED', 'TIMED_OUT', 'CANCELLED'}}


def transition(current: str, target: str) -> str:
    if target not in TRANSITIONS.get(current, set()):
        raise ValueError('Invalid execution state transition')
    return target
