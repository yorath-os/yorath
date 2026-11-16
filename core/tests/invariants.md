# Yorath invariant registry

What must not break, and why. The identifiers live only here: a test declares coverage with the marker `@pytest.mark.covers("INV-…")`, and an unknown identifier fails collection. Coverage: `make coverage-map`. A claim is not proof, so for the main invariants of each domain (⭐) a mutation is recorded below.

## Data and classes

| ID | Invariant | Why |
|---|---|---|
| INV-CLS-1 ⭐ | Text of a source the user or the agent may not read never reaches the model's context | The check happens before the LLM; a prompt is not a security boundary |
| INV-CLS-2 | The context class of an Execution only grows and equals the maximum of the data read | The label follows the data |
| INV-CLS-3 ⭐ | Conversation history carries its class: switching the model does not take an earlier answer outside | Found while building model choice |
| INV-CLS-4 | A document cannot have a class lower than its source | The class follows the data |

## Models

| ID | Invariant | Why |
|---|---|---|
| INV-MOD-1 ⭐ | A model call whose context is above the model's allowed class is blocked with a reason, without switching to another model | No silent rerouting (decision 9) |
| INV-MOD-2 | A model outside the agent's list is refused before the PDP and the provider | A model is an issued resource |
| INV-MOD-3 | Usage and cost of every call add up in the Execution attempt | Cost attribution |
| INV-MOD-4 | The provider gets an output limit when the caller did not set one | Otherwise HTTP 402 and unbounded cost |
| INV-MOD-5 | A runner crash or a model silent before the first token is retried once; the second in a row is an honest error | Found in measurements |
| INV-MOD-6 | The key of an external model is taken only from the vault and only with consent | Decision 9 |
| INV-MOD-7 | A model that does not fit the memory budget is not loaded without confirmation; switching the model unloads the previous one | Runner crashes on 8 GB |
| INV-MOD-8 | A broken model call (timeout, provider error) shows in the `llm.called` audit with status `failed` | A GPU run showed the timeout only as an empty span |

## Tools

| ID | Invariant | Why |
|---|---|---|
| INV-TOOL-1 ⭐ | A tool outside the agent's header does not run | "An agent has no tools but the ones it was given" |
| INV-TOOL-2 ⭐ | With a context above the tool's ceiling, no request goes outside | "Confidential does not leave" |
| INV-TOOL-3 | A call outside is in the audit with the domain and without the request text | The journal is the evidence |
| INV-TOOL-4 | Without a key, an external tool sends nothing and says so | Local mode |

## Records

| ID | Invariant | Why |
|---|---|---|
| INV-REC-1 ⭐ | A row the user has no right to is not in the result and not in the model's context | The check happens before the LLM |
| INV-REC-2 | A denied row is indistinguishable from a missing one; the denial shows only as a count in the audit | A denial must not confirm that a person exists |
| INV-REC-3 | Reading a row raises the Execution class: external calls are blocked | The label follows the data |
| INV-REC-4 | An allowed row is returned whole: all or nothing | Decision of 5.11.2026 |
| INV-REC-5 | An agent without the source gets no rows, whatever the user's rights | Permissions intersect |

## Agent-to-agent route

| ID | Invariant | Why |
|---|---|---|
| INV-AGT-1 ⭐ | Calling an agent with a context above its ceiling is blocked: the agent does not start, the child Execution is `denied`, the reason is in the journal | Isolation between agents |
| INV-AGT-2 | The child Execution keeps its parent, the same actor and effective principal, and the inherited class | Decision 27 |
| INV-AGT-3 | A recipient outside the header is refused | Decision 7 |
| INV-AGT-4 | A disabled agent does not start from a conversation or through `ask_agent`, and no Execution is created | The switch in the UI must not lie |
| INV-AGT-5 | A custom agent gets only allowed tools and existing sources; a refusal leaves no file and no record | Decision 31: there is one route between agents |
| INV-AGT-6 | A built-in agent cannot be changed from the panel; a custom file cannot take its name | Decision 31 |
| INV-AGT-7 | Creating and changing an agent is for the owner only and shows in the audit without the instruction text | Owner actions are audited |
| INV-AGT-8 | A custom file with an error does not stop the core from starting | Starting matters more than a file someone dropped in |
| INV-SBX-1 ⭐ | A request to the runner does not change the image, runtime, network, volumes or limits; the environment comes only from the list | The sandbox boundary |
| INV-SBX-2 | A runner without the right token runs nothing; an unavailable runtime gives an error, not a run without the sandbox | Decision 16 |
| INV-SBX-3 | A timeout kills the container, and the container is removed in any outcome | Adapter contract, rule 1 |
| INV-SBX-4 | A container's traffic outside shows in the audit by its address only | Audit at the boundary |
| INV-DEV-1 ⭐ | The Developer Agent starts only from a signed event with the label and the repository from the settings | Someone else's text does not run code |
| INV-DEV-2 ⭐ | Outcome and PR only when GitHub shows commits on the branch; the model's text is not proof | Adapter contract rule 3, Execution ≠ Outcome |
| INV-DEV-3 | The GitHub token is limited to the repository, revoked in any outcome and never in the journal | Decision 16 |
| INV-DEV-4 | One webhook delivery opens at most one Execution; a delivery without an open Execution is retried | Found on a test server |

## Execution and journal

| ID | Invariant | Why |
|---|---|---|
| INV-EXEC-1 ⭐ | Every outcome closes the Execution: a dropped subscription does not cancel the answer and the answer is recorded; a stop by the user or the core is `cancelled`; a core restart sweeps | Found: endless `running`, an answer lost when leaving the page |
| INV-EXEC-2 | An Outcome is created only when the model answers | Execution ≠ Outcome |
| INV-EXEC-3 | A question leaves a chain of spans and audit events | One journal for every action |
| INV-AUD-1 ⭐ | The audit is immutable: UPDATE and DELETE are refused | Decision 28 |
| INV-AUD-2 | Prompts, answers, tool arguments and results never reach the audit | The audit holds facts, not content |
| INV-AUD-3 | A policy denial is recorded with actor, principal, agent and reason | One journal for every action |
| INV-AUD-4 | A data upload is written to the audit without its content | The audit holds facts, not content |
| INV-AUD-5 | For any Execution the journal shows the chain actor → principal → revision → model → tool → asset → decision, and the parent with its children | One journal for every action |

## Identity and access

| ID | Invariant | Why |
|---|---|---|
| INV-ID-1 ⭐ | A revoked or expired session is not accepted; the database holds only the token hash | Sign-in |
| INV-ID-2 ⭐ | Owner routes are closed to `member` | Roles |
| INV-ID-3 | Someone else's conversation is not available | Users are isolated |
| INV-ID-4 | A password is stored only as an Argon2 hash with a salt | Passwords |
| INV-ID-5 | The owner can be created only with the setup code | Installation |
| INV-POL-1 | An invalid policy set is not applied | Decision 14 |
| INV-POL-2 | Permissions equal the intersection of the user, the agent and the data | Effective permissions |
| INV-SEC-1 | Secrets are encrypted and cannot be read with another key | Secrets |
| INV-GW-1 ⭐ | The Gateway HTTP endpoint accepts only a live Execution token | Decision 21 |
| INV-GW-2 | Calls through the HTTP endpoint go through the same checks and journal | One PEP for both control levels |
| INV-KEY-1 ⭐ | The endpoint accepts only a valid key of an active user; the database holds only the hash, the value is shown once | Decision 32 |
| INV-API-1 ⭐ | A call through the endpoint leaves no request text, answer or arguments in the trace and the audit | The endpoint does not store content |
| INV-API-2 | Only the models of the `model-api` card are available through the endpoint; an external model is refused without calling the provider | Local models only by default |
| INV-API-3 | Every endpoint call is a `model-api` Execution on behalf of the key owner, closed in any outcome | Decision 32 |
| INV-API-4 ⭐ | A key narrowed to an agent calls only that agent on behalf of the key owner: models and other agents are refused without a call | Issue #10 |
| INV-NET-1 ⭐ | Node traffic went around Yorath (rules removed or changed, the proxy silent): an event in the audit, and the recovery too | Decision 36 |
| INV-CON-1 ⭐ | A disabled connected agent gets no model answer with its key: refused without calling the model, the agent's Execution is `denied` in the journal | Decision 34 |
| INV-CON-2 ⭐ | The node service runs only an image from the catalog repository and only without privileges: `cap_drop: ALL`, `no-new-privileges`, the agents network, its own volume | Decision 34: the Docker socket is root on the host |
| INV-CON-3 | The model key, the sign-in token and the agent's bot token never appear in API answers, the audit or command output | The agent's secret boundary |
| INV-CON-4 | One Telegram bot belongs to one agent | Telegram delivers a bot's updates to one receiver |
| INV-CLU-1 ⭐ | A revoked or unapproved node gets no desired state and cannot report | Decision 35: revoking a node is instant |
| INV-CLU-2 ⭐ | A node gets and updates only its own Deployments | A node must not see other agents and their secrets |
| INV-CLU-3 | Joining beyond 3 Community units is refused; joined nodes keep working | Decision 35, the Community limit |
| INV-PLC-1 | A model is downloaded to a node once and placed on every chosen model server; a node has one server per GPU | Decision 44; checked on a two-GPU node |
| INV-PLC-2 | A call goes to the ready placement with the fewest calls in progress | Decision 44: otherwise the second GPU sits idle |
| INV-PLC-3 ⭐ | A model without a ready placement on an active node answers `model_unavailable`; no other model is called, even a ready one | No silent model switch when the catalog has several models |
| INV-PLC-4 ⭐ | A model is not placed on a node that does not allow at least one of its data classes, and a node's classes cannot be narrowed under a model placed there | Decision 44: data does not go to a node not trusted with its class |
| INV-QUE-1 ⭐ | A local model call goes to a model server only when it has a free slot; the rest wait in a queue on the main node, and the key, model list and data class checks run before the queue | Decision 44: waiting is visible and managed in one place; a refused call takes no place |
| INV-QUE-2 ⭐ | A model's queue is served round-robin between participants, in arrival order within a participant | Decision 44: one client's burst does not hold the others |
| INV-QUE-3 | A server that could not be reached is out of the choice until the node's heartbeat; a call without a single chunk of answer is retried once on another placement of the same model and never on another model | Decision 44: the node died but its heartbeat has not expired yet |

## Distribution

| ID | Invariant | Why |
|---|---|---|
| INV-DIST-1 | The manifest schema version equals the Alembic head | Decision 22 |
| INV-DIST-2 | The Ollama limit is computed from the node's memory, not below the minimum | Found in a measurement |
| INV-DIST-3 | Bootstrap passes on a clean database | Installation |

## Mutations

Break production code, watch the test fail, roll back. Filled in by `tests/mutations.py`.

<!-- mutations -->

| Invariant | Mutation | File | Result |
|---|---|---|---|
| INV-CLS-1 | search does not filter sources by the PDP decision | `src/yorath/runtime/rag/search.py` | failed `test_search_filters_by_agent_and_class` |
| INV-CLS-3 | a new Execution does not inherit the conversation class | `src/yorath/runtime/chat/service.py` | failed `test_history_class_blocks_external_model_after_switch` |
| INV-MOD-1 | a PDP deny on call_model is ignored | `src/yorath/enforcement/gateway/models.py` | failed `test_model_denied_by_context_class` |
| INV-TOOL-1 | a PDP deny on use_tool is ignored | `src/yorath/enforcement/gateway/tools.py` | failed `test_tool_outside_card_is_denied_by_pdp` |
| INV-TOOL-2 | the tool-class-ceiling policy does not fire | `src/yorath/enforcement/policies/agents.cedar` | failed `test_confidential_context_blocks_web_search` |
| INV-REC-1 | the PDP decision on a row is ignored | `src/yorath/runtime/tools/query_records.py` | failed `test_each_user_sees_only_allowed_namesakes[ivanov-expected0-absent0]` |
| INV-AGT-4 | a disabled agent starts | `src/yorath/enforcement/gateway/context.py` | failed `test_disabled_agent_does_not_run_in_open_conversation` |
| INV-DEV-1 | a wrong signature is accepted | `src/yorath/runtime/developer/github.py` | failed `test_webhook_refuses_bad_signature` |
| INV-DEV-2 | a PR is created without commits | `src/yorath/runtime/developer/service.py` | failed `test_success_claimed_without_commits_gives_no_outcome` |
| INV-MOD-8 | a broken model call is not written to the audit | `src/yorath/enforcement/gateway/models.py` | failed `test_silent_model_twice_is_failed_call_in_audit` |
| INV-DEV-4 | a repeated delivery opens a second Execution | `src/yorath/runtime/developer/router.py` | failed `test_same_delivery_runs_once` |
| INV-SBX-1 | the request passes any environment to the container | `src/yorath/enforcement/sandbox/runner.py` | failed `test_request_cannot_change_boundary` |
| INV-AGT-5 | a custom agent gets any tool | `src/yorath/control/agents/custom.py` | failed `test_refused_form_leaves_nothing[change0-tool_not_allowed]` |
| INV-AGT-1 | a PDP deny on invoke_agent is ignored | `src/yorath/enforcement/gateway/agents.py` | failed `test_confidential_context_blocks_research_agent` |
| INV-EXEC-1 | a dropped subscription cancels the answer | `src/yorath/runtime/chat/runs.py` | failed `test_client_disconnect_does_not_cancel_answer` |
| INV-EXEC-1 | a stopped answer does not close the Execution | `src/yorath/runtime/chat/service.py` | failed `test_cancel_stops_answer_and_records_message` |
| INV-AUD-1 | the trigger does not refuse DELETE in the audit | `alembic/versions/0002_domain_model.py` | failed `test_audit_is_append_only` |
| INV-ID-1 | a revoked session is accepted | `src/yorath/control/identity/service.py` | failed `test_setup_then_login_logout_flow` |
| INV-ID-2 | the role check is off | `src/yorath/control/identity/deps.py` | failed `test_member_gets_403` |
| INV-GW-1 | an expired Execution token is accepted | `src/yorath/enforcement/gateway/tokens.py` | failed `test_token_required_and_validated` |
| INV-API-1 | the endpoint prompt is written to the trace | `src/yorath/enforcement/gateway/models.py` | failed `test_gateway_skips_content_when_asked[False]` |
| INV-KEY-1 | a revoked API key is accepted | `src/yorath/control/identity/api_keys.py` | failed `test_owner_sees_all_and_revokes_member_key` |
| INV-API-4 | an agent key opens the bare model | `src/yorath/enforcement/gateway/public.py` | failed `test_agent_key_opens_nothing_else` |
| INV-API-3 | the client leaving after the last chunk breaks accounting | `src/yorath/enforcement/gateway/public.py` | failed `test_client_leaving_after_last_chunk_still_closes_execution[/v1/chat/completions-body0-"finish_reason":` |
| INV-NET-1 | removed network rules do not reach the audit | `src/yorath/control/journal/egress.py` | failed `test_protection_changes_are_audited` |
| INV-CON-1 | a disabled connected agent gets the model with its key | `src/yorath/enforcement/gateway/public.py` | failed `test_disabled_agent_with_live_key_gets_denied_execution` |
| INV-CON-2 | the node service runs an image from another repository | `src/yorath/runtime/connected/node_agent.py` | failed `test_image_outside_catalog_repository_is_refused` |
| INV-CLU-1 | a revoked node gets the desired state | `src/yorath/control/agents/connected_router.py` | failed `test_revoked_node_loses_token_peer_and_agents` |
| INV-CLU-2 | a node gets and accepts other nodes' Deployments | `src/yorath/control/agents/connected.py` | failed `test_each_node_gets_only_its_deployments` |
| INV-PLC-3 | the call goes to a placement on an unavailable node instead of a refusal | `src/yorath/control/cluster/placements.py` | failed `test_model_without_ready_placement_is_refused_and_no_other_model_answers` |
| INV-PLC-4 | a model is placed on a node that does not allow its data class | `src/yorath/control/cluster/placements.py` | failed `test_model_is_not_placed_on_node_that_does_not_allow_its_data_class` |
| INV-QUE-1 | the call goes to a busy model server instead of the queue | `src/yorath/enforcement/gateway/slots.py` | failed `test_call_waits_on_the_main_node_until_a_slot_frees` |
| INV-QUE-2 | one participant's burst is served before another's call | `src/yorath/enforcement/gateway/slots.py` | failed `test_burst_of_one_participant_does_not_hold_another` |
