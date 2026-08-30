# Multi-Framework Durability Adapter Suite

LetItLoop provides native, drop-in durability adapters across the 4 major Python AI agent ecosystems:
1. **CrewAI** (`CrewAIDurabilityHandler`)
2. **Hugging Face Smolagents** (`SmolagentsWALCallback`)
3. **Microsoft AutoGen 0.4 / Magentic-One** (`AutoGenStateSerializer`)
4. **LangGraph** (`LetItLoopCheckpointSaver`)

All adapters feature **zero mandatory runtime dependencies** (lazy-loaded optional imports), ensuring that `pip install letitloop` stays ultralight while providing full crash resilience.

---

## 1. 👥 CrewAI: `CrewAIDurabilityHandler`

Captures task execution boundaries, agent assignments, and tool invocations into an atomic Write-Ahead Log. If a Crew execution crashes mid-way, initializing the handler automatically resumes from the last completed task.

### Quickstart
```python
from crewai import Agent, Crew, Task
from letitloop.adapters.crewai import CrewAIDurabilityHandler

# 1. Initialize durability handler
handler = CrewAIDurabilityHandler(
    wal_dir=".letitloop/crewai_wal",
    session_id="financial_research_crew",
    auto_resume=True
)

# 2. Define standard CrewAI tasks
task_research = Task(description="Fetch AAPL financials", expected_output="Financial metrics", agent=analyst)
task_report = Task(description="Write investment thesis", expected_output="Markdown report", agent=writer)

# 3. Attach handler to tasks & crew
crew = Crew(
    agents=[analyst, writer],
    tasks=[task_research, task_report]
)
handler.wrap_crew(crew)

# 4. Kickoff with crash resilience
result = crew.kickoff()
```

---

## 2. 🤗 Hugging Face Smolagents: `SmolagentsWALCallback`

Integrates directly into Hugging Face `smolagents` (`CodeAgent`, `ToolCallingAgent`). Captures step-level reasoning, generated code actions, observations, and tool executions.

### Quickstart
```python
from smolagents import CodeAgent, HfApiModel
from letitloop.adapters.smolagents import SmolagentsWALCallback

# 1. Initialize step callback
wal_callback = SmolagentsWALCallback(
    wal_dir=".letitloop/smolagents_wal",
    session_id="math_reasoning_agent",
    auto_resume=True
)

# 2. Pass callback directly to Agent
agent = CodeAgent(
    tools=[],
    model=HfApiModel(),
    step_callbacks=[wal_callback]
)

# 3. Run agent with step-by-step WAL persistence
response = agent.run("Calculate the compound interest for $10,000 at 5% over 10 years.")
```

---

## 3. 🤖 Microsoft AutoGen 0.4: `AutoGenStateSerializer`

Provides event-sourced state checkpointing and message history preservation for Microsoft AutoGen 0.4 (AgentChat / Magentic-One) and legacy `ConversableAgent` setups.

### Quickstart
```python
from letitloop.adapters.autogen import AutoGenStateSerializer

# 1. Initialize state serializer
serializer = AutoGenStateSerializer(
    wal_dir=".letitloop/autogen_wal",
    session_id="groupchat_dev_sprint",
    auto_resume=True
)

# 2. Wrap agents for transparent message checkpointing
serializer.wrap_agent(user_proxy, agent_name="UserProxy")
serializer.wrap_agent(coder, agent_name="CoderAgent")

# 3. Explicit state checkpointing
serializer.save_agent_state("CoderAgent", {"memory": ["step 1", "step 2"]})
```

---

## 4. 🕸️ LangGraph: `LetItLoopCheckpointSaver`

Implements LangGraph's native `BaseCheckpointSaver` interface using an embedded SQLite database configured in high-performance atomic WAL mode (`PRAGMA journal_mode=WAL`). Provides sub-millisecond superstep checkpointing with zero external database daemons.

### Quickstart
```python
from langgraph.graph import StateGraph, START, END
from letitloop.adapters.langgraph import LetItLoopCheckpointSaver

# 1. Initialize SQLite WAL checkpointer
checkpointer = LetItLoopCheckpointSaver(wal_dir=".letitloop/langgraph_checkpoints")

# 2. Define StateGraph
builder = StateGraph(dict)
builder.add_node("fetch", fetch_data_node)
builder.add_node("process", process_data_node)
builder.add_edge(START, "fetch")
builder.add_edge("fetch", "process")
builder.add_edge("process", END)

# 3. Compile with LetItLoop checkpointer
app = builder.compile(checkpointer=checkpointer)

# 4. Invoke graph with thread_id
config = {"configurable": {"thread_id": "session_001"}}
result = app.invoke({"input": "query"}, config=config)
```
