"""PDP scenarios: entities and expected decisions. Shared by unit and group tests."""

import uuid

from yorath.control.agents.loader import AgentCard
from yorath.control.model import (
    Agent,
    AgentMode,
    AssetKind,
    DataAsset,
    DataClass,
    Hosting,
    ModelProvider,
    ModelResource,
    ServiceAccount,
    User,
)
from yorath.enforcement import entities as E
from yorath.enforcement.pdp import AuthzRequest, EntityRef


def user(login, role, department, manager=None, **attrs):
    return User(
        id=uuid.uuid7(),
        login=login,
        display_name=login,
        role=role,
        department=department,
        manager_id=manager.id if manager else None,
        attributes=attrs,
    )


def agent(name, mode, card):
    a = Agent(id=uuid.uuid7(), name=name, display_name=name, hosting=Hosting.MANAGED, mode=mode,
              runtime_type_id=uuid.uuid7())  # fmt: skip
    return a, AgentCard.model_validate({"name": name, "mode": mode.value, **card})


PUBLIC, INTERNAL, CONFIDENTIAL, RESTRICTED = 0, 1, 2, 3


DA, DA_CARD = agent(
    "document-assistant",
    AgentMode.DELEGATED,
    {
        "data": ["hr-vacations", "hr-policy", "team-docs", "fin-docs"],
        "tools": ["search_docs", "query_records", {"ask_agent": ["research-agent"]}],
        "max_data_class": "confidential",
    },
)
RESEARCH, RESEARCH_CARD = agent(
    "research-agent",
    AgentMode.DELEGATED,
    {"data": [], "tools": ["web_search"], "max_data_class": "internal"},
)
PAGEY, PAGEY_CARD = agent(
    "pagey",
    AgentMode.AUTONOMOUS,
    {"runtime": "codex", "data": [], "tools": ["git"], "max_data_class": "internal"},
)


def asset(name, data_class, roles=(), departments=(), kind=AssetKind.DOCUMENTS, owner=None):
    return DataAsset(
        id=uuid.uuid7(),
        name=name,
        kind=kind,
        data_class=data_class,
        access_rules={"roles": list(roles), "departments": list(departments)},
        owner_id=owner.id if owner else None,
    )


ALEX = user("alex", "owner", "")


SOKOLOV = user("sokolov", "manager", "top")


LEBEDEV = user("lebedev", "manager", "dev", SOKOLOV)


EGOROV = user("egorov", "manager", "fin", SOKOLOV)


IVANOV = user("ivanov", "employee", "dev", LEBEDEV)


PETROVA = user("petrova", "employee", "dev", LEBEDEV)


ORLOVA = user("orlova", "hrbp", "hr", SOKOLOV, assigned_departments=["dev", "sales"])


VOLKOVA = user("volkova", "employee", "fin", EGOROV)


USERS = [ALEX, SOKOLOV, LEBEDEV, EGOROV, IVANOV, PETROVA, ORLOVA, VOLKOVA]


HR_ROLES = ["employee", "manager", "hrbp", "hr_admin"]


VACATIONS = asset("hr-vacations", DataClass.CONFIDENTIAL, HR_ROLES, kind=AssetKind.RECORDS)


HR_POLICY = asset("hr-policy", DataClass.INTERNAL, HR_ROLES)


TEAM_DOCS = asset("team-docs", DataClass.INTERNAL, ["employee", "manager"], owner=LEBEDEV)


FIN_DOCS = asset("fin-docs", DataClass.INTERNAL, departments=["fin"])


REC_IVANOV = uuid.uuid7()


REC_VOLKOVA = uuid.uuid7()


PAGEY_SVC = ServiceAccount(
    id=uuid.uuid7(), display_name="pagey-svc", owner_user_id=ALEX.id, agent_id=PAGEY.id
)


LOCAL = ModelResource(name="local-default", provider=ModelProvider.OLLAMA, model_id="gemma4:e2b",
                      allowed_data_classes=[c.value for c in DataClass])  # fmt: skip


OPENROUTER = ModelResource(name="openrouter-gpt", provider=ModelProvider.OPENROUTER,
                           model_id="openai/gpt", allowed_data_classes=["public", "internal"])  # fmt: skip


ENTITIES = [
    *(E.user_entity(u) for u in USERS),
    E.service_account_entity(PAGEY_SVC, "pagey"),
    *(E.data_asset_entity(a) for a in [VACATIONS, HR_POLICY, TEAM_DOCS, FIN_DOCS]),
    E.record_entity(
        "hr-vacations",
        REC_IVANOV,
        DataClass.CONFIDENTIAL,
        employee_id=IVANOV.id,
        department="dev",
        manager_id=LEBEDEV.id,
    ),  # fmt: skip
    E.record_entity(
        "hr-vacations",
        REC_VOLKOVA,
        DataClass.CONFIDENTIAL,
        employee_id=VOLKOVA.id,
        department="fin",
        manager_id=EGOROV.id,
    ),  # fmt: skip
    E.agent_entity(DA, DA_CARD),
    E.agent_entity(RESEARCH, RESEARCH_CARD),
    E.agent_entity(PAGEY, PAGEY_CARD),
    E.model_entity(LOCAL),
    E.model_entity(OPENROUTER),
    E.tool_entity("search_docs"),
    E.tool_entity("web_search"),
    E.tool_entity("git"),
]


def U(u):
    return EntityRef(type="User", id=str(u.id))


def R(rec):
    return EntityRef(type="Record", id=str(rec))


def req(principal, action, resource, agent="document-assistant", context_class=INTERNAL):
    return AuthzRequest.build(principal, action, resource, agent=agent, context_class=context_class)


SCENARIOS = [
    # criterion 4: one question, different answers
    ("ivanov-own-record", req(U(IVANOV), "query_record", R(REC_IVANOV)), True, ["employee-own-record"]),
    ("petrova-foreign-record", req(U(PETROVA), "query_record", R(REC_IVANOV)), False, []),
    ("lebedev-direct-report", req(U(LEBEDEV), "query_record", R(REC_IVANOV)), True, ["manager-direct-reports"]),
    ("lebedev-other-department", req(U(LEBEDEV), "query_record", R(REC_VOLKOVA)), False, []),
    ("orlova-assigned-dev", req(U(ORLOVA), "query_record", R(REC_IVANOV)), True, ["hrbp-assigned-departments"]),
    ("orlova-not-assigned-fin", req(U(ORLOVA), "query_record", R(REC_VOLKOVA)), False, []),
    ("owner-reads-any-record", req(U(ALEX), "query_record", R(REC_VOLKOVA)), True, ["owner-reads-all"]),
    # intersection: the user may, the agent may not
    ("ivanov-via-research-agent", req(U(IVANOV), "query_record", R(REC_IVANOV), agent="research-agent"),
     False, ["agent-class-ceiling", "agent-not-allowed-for-asset"]),
    ("owner-via-research-agent", req(U(ALEX), "read_doc", EntityRef(type="DataAsset", id="team-docs"),
                                     agent="research-agent"), False, ["agent-not-allowed-for-asset"]),
    ("hrbp-team-docs-role-missing", req(U(ORLOVA), "read_doc", EntityRef(type="DataAsset", id="team-docs")), False, []),
    ("volkova-fin-docs-by-department", req(U(VOLKOVA), "read_doc", EntityRef(type="DataAsset", id="fin-docs")),
     True, ["doc-by-department"]),
    ("lebedev-owns-team-docs", req(U(LEBEDEV), "read_doc", EntityRef(type="DataAsset", id="team-docs")),
     True, ["doc-by-role", "doc-owner"]),
    # agent tools
    ("ivanov-search-docs", req(U(IVANOV), "use_tool", EntityRef(type="Tool", id="search_docs")), True, ["tools-for-everyone"]),
    ("ivanov-web-search-via-da", req(U(IVANOV), "use_tool", EntityRef(type="Tool", id="web_search")),
     False, ["agent-tool-not-allowed"]),
    # criterion 6: model and context class
    ("da-local-confidential", req(U(IVANOV), "call_model", EntityRef(type="Model", id="local-default"),
                                  context_class=CONFIDENTIAL), True, ["model-allowed-class"]),
    ("da-openrouter-confidential", req(U(IVANOV), "call_model", EntityRef(type="Model", id="openrouter-gpt"),
                                       context_class=CONFIDENTIAL), False, []),
    ("da-openrouter-internal", req(U(IVANOV), "call_model", EntityRef(type="Model", id="openrouter-gpt"),
                                   context_class=INTERNAL), True, ["model-allowed-class"]),
    # criterion 5: inter-agent route
    ("da-to-research-internal", req(U(IVANOV), "invoke_agent", EntityRef(type="Agent", id="research-agent"),
                                    context_class=INTERNAL), True, ["agent-to-agent"]),
    ("da-to-research-confidential", req(U(IVANOV), "invoke_agent", EntityRef(type="Agent", id="research-agent"),
                                        context_class=CONFIDENTIAL), False, []),
    ("research-to-da-not-allowed", req(U(IVANOV), "invoke_agent", EntityRef(type="Agent", id="document-assistant"),
                                       agent="research-agent", context_class=PUBLIC), False, []),
    # service account: only through its own agent
    ("pagey-svc-git-via-pagey", req(EntityRef(type="ServiceAccount", id=str(PAGEY_SVC.id)), "use_tool",
                                    EntityRef(type="Tool", id="git"), agent="pagey"), True, ["tools-for-everyone"]),
    ("pagey-svc-via-other-agent", req(EntityRef(type="ServiceAccount", id=str(PAGEY_SVC.id)), "use_tool",
                                      EntityRef(type="Tool", id="search_docs")), False,
     ["service-account-own-agent"]),
]  # fmt: skip
