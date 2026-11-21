from dataclasses import dataclass

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from eduflow.adapters.authentication import AuthConfig, Authentication
from eduflow.adapters.cp_sat import CpSatScheduler
from eduflow.adapters.model_gateway import CompatibleModelGateway
from eduflow.adapters.repository import SqlUnitOfWork
from eduflow.adapters.system import SystemClock
from eduflow.application.agent import AgentTools, ChatAgent
from eduflow.application.candidate_cleanup import DeleteDraftVersion
from eduflow.application.changes import CancelChange, CreateChangeRequest, DecideChange, SimulateChange
from eduflow.application.commands import CommandHandler
from eduflow.application.copy_week import CopyWeek
from eduflow.application.enrollment import EnrollStudent, ReviewOffering
from eduflow.application.operations import (
    EmergencyPause,
    InviteOfferingReview,
    MarkNotificationRead,
    RecordAttendance,
    RecordAttendanceBatch,
    RecordFeedback,
    SaveAvailability,
    SaveQualifications,
    SaveResource,
    SaveRules,
    SaveUser,
)
from eduflow.application.ports import Clock, UnitOfWorkFactory
from eduflow.application.queries import Queries
from eduflow.application.scheduling import (
    CancelVersion,
    ConfirmSchedule,
    EditDraftSession,
    PublishVersion,
    RunSolveJob,
    SolveWeek,
    ValidateVersion,
)
from eduflow.bootstrap.settings import Settings


@dataclass(frozen=True)
class Platform:
    engine: Engine
    factory: sessionmaker[Session]
    uow: UnitOfWorkFactory
    auth: Authentication
    queries: Queries
    commands: dict[str, CommandHandler]
    simulate: SimulateChange
    runner: RunSolveJob
    agent: ChatAgent
    clock: Clock


def build_platform(settings: Settings, clock: Clock | None = None) -> Platform:
    clock = clock or SystemClock()
    engine = create_engine(settings.database_url, pool_pre_ping=True, pool_size=10, max_overflow=15)
    factory = sessionmaker(engine, expire_on_commit=False)

    def uow(organization_id: str) -> SqlUnitOfWork:
        return SqlUnitOfWork(factory, organization_id)

    queries = Queries(uow, clock)
    scheduler = CpSatScheduler()
    simulate = SimulateChange(uow, clock, scheduler)
    handlers = [
        EnrollStudent,
        ReviewOffering,
        SolveWeek,
        ValidateVersion,
        PublishVersion,
        ConfirmSchedule,
        EditDraftSession,
        CancelVersion,
        DeleteDraftVersion,
        CreateChangeRequest,
        DecideChange,
        CancelChange,
        SaveResource,
        SaveUser,
        SaveAvailability,
        SaveQualifications,
        SaveRules,
        RecordAttendance,
        RecordAttendanceBatch,
        RecordFeedback,
        MarkNotificationRead,
        EmergencyPause,
        CopyWeek,
        InviteOfferingReview,
    ]
    commands = {handler.__name__: handler(uow, clock) for handler in handlers}
    tools = AgentTools(uow, clock, queries, SolveWeek(uow, clock), CreateChangeRequest(uow, clock), simulate)
    gateway = (
        CompatibleModelGateway(settings.llm_base_url, settings.llm_model, settings.llm_api_key)
        if settings.agent_mode == "online"
        and all((settings.llm_base_url, settings.llm_model, settings.llm_api_key))
        else None
    )
    auth = Authentication(
        factory,
        AuthConfig(
            settings.signing_key,
            settings.jwt_issuer,
            settings.organization_slug,
            settings.access_minutes,
            settings.refresh_days,
            settings.portal,
            settings.audience,
        ),
        clock,
    )
    return Platform(
        engine,
        factory,
        uow,
        auth,
        queries,
        commands,
        simulate,
        RunSolveJob(uow, clock, scheduler),
        ChatAgent(tools, queries, clock, gateway),
        clock,
    )
