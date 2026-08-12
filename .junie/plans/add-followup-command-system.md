---
sessionId: session-260812-071155-1g55
---

# Requirements

### Overview & Goals
The goal is to implement a new command/phase called `followup` that allows discovery of new freelancers based on the names of existing ones. This "followup" mechanism will extract the first word from names in a provided JSON file, de-duplicate them, and then use them as search keywords on Mostaql.

### Scope
- **In Scope:**
    - New `followup` command in `main.py`.
    - New `followup` stage in the pipelined runner.
    - Extraction of unique first words from names in `mostaql_development_all_users.json`.
    - Extension of `ComboManager` to support keyword-based URL construction.
    - Integration with the existing `extract`, `fetch`, and `parse` stages.
- **Out of Scope:**
    - Modifying existing discovery logic (which is based on filter combinations).
    - Database integration (the request mentions "it will have db", but the existing system uses JSON/CSV as its "database"). I will stick to the existing file-based storage patterns unless a specific DB engine is requested.

# Technical Design

### Current Implementation
The system is a 4-phase pipeline:
1.  **Discovery:** Finds page counts for filter combos.
2.  **Extraction:** Scrapes listing pages for URLs.
3.  **Fetch:** Downloads raw HTML.
4.  **Parse:** Turns HTML into structured data.

It uses `asyncio` channels to stream items between stages.

### Proposed Changes

#### 1. Data Models (`src/models.py`)
Add a new milestone item:
```python
@dataclass(frozen=True)
class KeywordItem:
    """Milestone emitted by the followup stage for one search keyword."""
    keyword: str
    combo: Dict[str, Any]
```

#### 2. Combo Management (`src/utils/combos.py`)
Update `get_url` to handle a `keyword` parameter in the combo:
```python
def get_url(self, combo: Dict[str, Any], page: int = 1) -> str:
    params = dict(self.FIXED_PARAMS)
    params.update(combo.get("params", {}))
    if "keyword" in combo:
        params["keyword"] = combo["keyword"]
    # ... rest of logic
```

#### 3. Orchestrator Logic (`src/services/orchestrator.py`)
Add `stream_followup`:
- Load `mostaql_development_all_users.json`.
- Extract `name.split()[0]` for each entry.
- Get unique names.
- For each unique name, perform a "mini-discovery" (find page count) or directly stream to extraction if we assume a small number of pages.
- Emit `KeywordItem`.

Update `stream_extraction` to accept `KeywordItem` as an alternative to `PageCountItem`.

#### 4. Pipeline Spec (`src/pipeline/spec.py`)
Register the new stage:
```python
"followup": StageSpec(
    name="followup",
    positions=frozenset({StagePosition.START}),
    method="stream_followup",
    input_type=None,
    output_type=KeywordItem,
    description="Extract unique names from existing data and prepare search keywords.",
),
```
Update `extract` to support `input_type=Union[PageCountItem, KeywordItem]`.

### File Structure
- `src/models.py`: Add `KeywordItem`.
- `src/utils/combos.py`: Update `get_url`.
- `src/services/orchestrator.py`: Add `stream_followup`, update `stream_extraction`.
- `src/pipeline/spec.py`: Register `followup`.
- `main.py`: Add `followup` command.

### Architecture Diagram
```mermaid
graph LR
    Input[mostaql_development_all_users.json] --> Followup[Followup Stage]
    Followup -- KeywordItem --> Extract[Extraction Stage]
    Extract -- Freelancer --> Fetch[Fetch Stage]
    Fetch -- RawProfileRecord --> Parse[Parse Stage]
    Parse --> Output[Profiles JSON/CSV]
```


# Testing

### Validation Approach
I will verify the new command by running it in dry-run mode (if possible) or by checking the logs to ensure it correctly extracts unique names and constructs the search URLs.

### Key Scenarios
- **Standalone Followup:** `python main.py followup` should read the file and report unique names found.
- **Pipelined Followup:** `python main.py followup --pipelined extract` should perform searches for the extracted names.
- **Full Chain:** `python main.py followup --pipelined extract --pipelined fetch --pipelined parse` should result in new users being parsed and saved.

### Edge Cases
- **Missing Input File:** Handle `FileNotFoundError` gracefully.
- **Empty Names:** Skip records with missing or empty names.
- **Duplicates:** Ensure unique names are processed only once.
- **Arabic Names:** Ensure URL encoding works correctly for Arabic keywords (as shown in the example URL).

# Delivery Steps

### ✓ Step 1: Update Models and Configuration
Add `KeywordItem` to `src/models.py` to represent a search term extracted from a freelancer's name.

- Define `KeywordItem(keyword: str, combo: Dict[str, Any])` in `src/models.py`.
- Update `ScrapeConfig` with default paths for the followup input file.

### ✓ Step 2: Implement Followup Logic in Orchestrator
Add `stream_followup` to `src/services/orchestrator.py` to implement the new Phase 0.

- Read unique first words from the input JSON file.
- Stream each unique name as a `KeywordItem` milestone.
- Add `_followup_worker` and support for `keyword` parameter in `ComboManager.get_url`.
- Implement `stream_extraction` support for `KeywordItem` input to perform the actual search.

### ✓ Step 3: Register Stage and Add CLI Command
Register the `followup` stage in `src/pipeline/spec.py` and expose the CLI command in `main.py`.

- Add `followup` to `STAGE_REGISTRY` with `KeywordItem` as output.
- Update `extract` stage spec to accept `KeywordItem` as input.
- Add `@app.command()` for `followup` in `main.py`.
- Update `examples` command to include a followup pipeline example.

### ✓ Step 4: Implement Fixup Command
Implement a `fixup` command to correct missing titles and ranks in existing JSON files.

- Add `fixup` stage to `src/pipeline/spec.py`.
- Implement `stream_fixup` in `src/services/orchestrator.py` to identify incomplete records and re-fetch them.
- Add `@app.command()` for `fixup` in `main.py`.