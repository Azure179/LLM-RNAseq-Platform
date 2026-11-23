# Architecture

## Two entry points

```mermaid
flowchart TD
    U[Local researcher] --> UI[Streamlit]
    U --> API[FastAPI]
    UI --> B[Shared Python services]
    API --> B
    B --> P[DeepSeek planner]
    P --> V[Local workflow validator]
    B --> V
    V --> E[Workflow executor]
    E --> R[Skill Registry]
    R --> S[Python and R Skills]
    S --> O[SkillResult and artifacts]
    B --> M[ResultManager and ResultResolver]
    M --> W[(Project filesystem)]
    O --> W
```

Explicit Skill requests use the registry without contacting a provider. Chat additionally has a single-tool route. A manifest describes inputs and capabilities; local code holds the executable binding.

## A multi-step request

```mermaid
flowchart LR
    T[User request] --> P[DeepSeek JSON plan]
    P --> V{Local validation}
    V -->|Missing or invalid| Q[Return structured feedback]
    V -->|Valid| D[Resolve inputs and topologically order DAG]
    D --> E[Execute steps through registry]
    E --> S[Step SkillResults]
    S --> W[WorkflowResult]
    W --> G[Grounded summary or local failure message]
```

Volcano and enrichment can be siblings of one DEG step. An upstream failure skips dependent steps. Independent branches can still complete.

## Which DEG can a new plot use?

```mermaid
flowchart TD
    H[Existing DEG in this project] --> R[ResultResolver]
    R --> C{Direction, parameters, artifacts and input version}
    C -->|Current| B[Bind verified parent run]
    C -->|Stale or ambiguous| F[Reject automatic selection]
    F --> X[Explicit eligible historical source]
    X --> Y[Record historical_run]
    Y --> B
    B --> V[Volcano]
    B --> GO[GO]
    B --> K[KEGG]
```

Historical selection still requires the needed source artifacts. Children carry the parent run ID; the old comparison keeps its original direction. For the explicit/automatic distinction see the [reuse guide](reproducibility.md).

## Deployment

```mermaid
flowchart LR
    Browser[Browser on localhost] -->|8501| UI[Streamlit container]
    Client[Local API client] -->|8000| API[FastAPI container]
    UI --> B1[Installed Python and R backend]
    API --> B2[Same image and backend]
    B1 --> W[(Shared /workspace volume)]
    B2 --> W
```

Both services access the volume directly. A short file lock protects shared metadata updates. R analysis runs outside that lock.

## Execution boundary

```mermaid
flowchart LR
    M[Model proposal] --> S[Local schema and source validation]
    S --> C[Controlled customization]
    C --> K[Reviewed plotting or filtering implementation]
    K --> A[New artifact with source run]
    H[Trusted host launcher] --> L[Fixed task allowlist]
    L --> W[Restricted worker with bounded resources]
    W --> O[Validated receipt]
```

The isolated-worker prototype has its own host launcher. Chat, FastAPI and Streamlit have no general shell or Docker execution endpoint. The worker receipt is infrastructure evidence. [Worker contract and limits](../container/isolated/README.md)
