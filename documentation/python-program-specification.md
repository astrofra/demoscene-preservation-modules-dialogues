# MODialogues Python Program Specification

## 1. Purpose

This document specifies a Python 3.9 program that implements the archival and analytical workflow described in `readme.md` and `about.md`.

The program must:

- discover tracker module files from one or more remote sources
- download new files and resume interrupted downloads
- parse embedded textual metadata from module files
- run a local Ollama model on extracted text
- detect greetings and mentions between scene members
- build exportable relationship graphs
- be safe to restart at any time without losing progress

The implementation must favor simple, readable Python over abstraction-heavy design.

## 2. Design Constraints

- Target runtime: Python 3.9
- Coding style: mostly procedural, small functions, little or no class hierarchy
- No async code
- No ORM
- No heavy framework
- Minimal defensive code
- Failures on one file must not stop the full batch
- Every stage must be idempotent and resumable

## 3. Scope

### In Scope

- remote file discovery
- incremental download and duplicate detection
- parsing `.mod`, `.xm`, `.s3m`, and `.it`
- metadata normalization
- local LLM summarization through Ollama HTTP API
- greeting / mention extraction
- graph export
- CLI scripts for running one stage or the whole pipeline

### Out of Scope for the First Implementation

- web UI
- audio playback
- distributed processing
- parallel workers
- automatic cloud deployment
- full scholarly annotation workflow

## 4. High-Level Architecture

The program should be a filesystem-based pipeline with a small set of JSON state files used as the central state store.

Reason for JSON state files:

- they are readable without any extra tool
- they are easy to inspect and edit manually
- they match the rest of the project outputs
- they keep the first version simple

The core rule is:

- raw files and generated artifacts live in `data/`
- pipeline state lives in a few JSON files under `data/state/`
- every stage reads pending work from the state files and writes updates back atomically
- the first version may rewrite the full state file after each processed item because simplicity is preferred over speed

## 5. Repository Layout

The implementation should use this structure:

```text
MODialogues/
|
+-- config/
|   +-- config.json
|   +-- instrument_terms.json
|   +-- rule_patterns.json
|
+-- data/
|   +-- raw_modules/
|   |   +-- sceneorg-artists/
|   |   |   +-- zipp/
|   |   |       +-- 1998/
|   |   |           +-- djz_poof.xm
|   |   +-- _partial/
|   +-- parsed_metadata/
|   +-- summaries/
|   +-- graphs/
|   +-- embeddings/          # Optional, not required for the first version
|   +-- logs/
|   +-- state/
|       +-- remote_files.json
|       +-- modules.json
|       +-- summaries.json
|
+-- scripts/
|   +-- fetch_modules.py
|   +-- parse_modules.py
|   +-- run_ollama.py
|   +-- build_graph.py
|   +-- run_pipeline.py
|   +-- common_state.py
|   +-- common_config.py
|   +-- common_utils.py
|
+-- documentation/
|   +-- python-program-specification.md
|
+-- readme.md
+-- about.md
```

The `common_*.py` files should contain plain helper functions only.

## 6. Configuration

The program should use one JSON configuration file: `config/config.json`.

Example structure:

```json
{
  "sources": [
    {
      "name": "sceneorg-artists",
      "type": "http_index",
      "base_url": "https://ftp.scene.org/pub/music/artists/"
    }
  ],
  "allowed_extensions": [".mod", ".xm", ".s3m", ".it"],
  "download_timeout_seconds": 60,
  "user_agent": "MODialogues/0.1",
  "classification": {
    "instrument_terms_path": "config/instrument_terms.json",
    "rule_patterns_path": "config/rule_patterns.json",
    "llm_min_useful_chars": 24,
    "llm_min_social_fragments": 1,
    "llm_skip_if_only_labels": [
      "instrument_only",
      "greeting",
      "signature",
      "work_offer",
      "contact",
      "technical_note"
    ]
  },
  "ollama": {
    "base_url": "http://127.0.0.1:11434",
    "model": "ministral",
    "timeout_seconds": 120
  },
  "embeddings": {
    "enabled": false,
    "base_url": "http://127.0.0.1:11434",
    "model": "qwen3-embedding"
  },
  "paths": {
    "state_dir": "data/state",
    "remote_files_state": "data/state/remote_files.json",
    "modules_state": "data/state/modules.json",
    "summaries_state": "data/state/summaries.json",
    "embeddings_dir": "data/embeddings"
  }
}
```

TOML should be avoided because Python 3.9 does not include a built-in TOML reader.
The state store should use plain JSON files, not JSONL, so they remain easy to read manually.

## 7. Pipeline State Model

The JSON state files are the source of truth for progress tracking.

### 7.1 Required State Files

Each state file should use this top-level structure:

```json
{
  "version": 1,
  "updated_at": "ISO-8601 timestamp",
  "items": []
}
```

The `items` list should be pretty-printed with `indent=2`.

#### `remote_files.json`

Tracks discovered files from remote sources.

Required item fields:

- `module_id`
- `source_name`
- `remote_path`
- `remote_url`
- `extension`
- `remote_size`
- `remote_mtime`
- `first_seen_at`
- `last_seen_at`
- `download_status` (`pending`, `done`, `failed`)
- `download_error`
- `local_path`
- `sha256`

Unique key:

- `(source_name, remote_path)`

#### `modules.json`

Tracks parsing results for one source file, identified by a stable internal ID.

Required item fields:

- `module_id`
- `sha256`
- `source_name`
- `remote_path`
- `local_path`
- `format`
- `parse_status` (`pending`, `done`, `failed`, `skipped`)
- `parse_error`
- `metadata_path`
- `title`
- `tracker_name`
- `author_guess`
- `author_source`
- `rule_labels`
- `llm_decision` (`run`, `skip`)
- `llm_reason`
- `text_fragment_count`
- `useful_fragment_count`
- `parsed_at`

Unique key:

- `module_id`

#### `summaries.json`

Tracks LLM output.

Required item fields:

- `module_id`
- `sha256`
- `source_name`
- `remote_path`
- `model_name`
- `prompt_version`
- `input_text_hash`
- `summary_status` (`pending`, `done`, `failed`, `skipped`)
- `summary_error`
- `summary_skip_reason`
- `summary_path`
- `tone`
- `mentions`
- `summarized_at`

Unique key:

- `module_id`

### 7.2 State Write Rules

The state layer must stay simple and resilient.

Rules:

- state files must be valid UTF-8 JSON
- write state to a temporary file first, then replace the previous file atomically
- update the relevant state file after each processed item
- creating a missing state file should initialize an empty structure
- if a state file is unreadable JSON, the program should stop and report the file path instead of guessing

Full-file rewrites are acceptable in the first version.

### 7.3 Resume Rules

The pipeline is resumable because each stage only selects items with pending or failed work.

Rules:

- discovery can be rerun at any time
- download skips items already marked `done` if the target file still exists
- parse skips module IDs already marked `done` if the metadata JSON still exists
- summarization skips module IDs already marked `done` if the summary JSON still exists
- graph building may rebuild from scratch each time because it is derived output

If a state item says `done` but the corresponding artifact file is missing, the stage must reset that item to `pending` and recreate the artifact.

### 7.4 Example State Files

Example `remote_files.json`:

```json
{
  "version": 1,
  "updated_at": "2026-06-23T10:00:00Z",
  "items": [
    {
      "module_id": "mod_7e31fd8d54e2",
      "source_name": "sceneorg-artists",
      "remote_path": "artists/traven/nytrik.mod",
      "remote_url": "https://ftp.scene.org/pub/music/artists/traven/nytrik.mod",
      "extension": ".mod",
      "remote_size": 52344,
      "remote_mtime": "1997-04-12T00:00:00Z",
      "first_seen_at": "2026-06-23T09:52:14Z",
      "last_seen_at": "2026-06-23T10:00:00Z",
      "download_status": "done",
      "download_error": null,
      "local_path": "data/raw_modules/sceneorg-artists/artists/traven/nytrik.mod",
      "sha256": "8b4d...c1"
    }
  ]
}
```

Example `modules.json`:

```json
{
  "version": 1,
  "updated_at": "2026-06-23T10:05:00Z",
  "items": [
    {
      "module_id": "mod_7e31fd8d54e2",
      "sha256": "8b4d...c1",
      "source_name": "sceneorg-artists",
      "remote_path": "artists/traven/nytrik.mod",
      "local_path": "data/raw_modules/sceneorg-artists/artists/traven/nytrik.mod",
      "format": "mod",
      "parse_status": "done",
      "parse_error": null,
      "metadata_path": "data/parsed_metadata/sceneorg-artists/artists/traven/nytrik.mod.json",
      "title": "Why not call?",
      "tracker_name": "ProTracker",
      "author_guess": "Traven",
      "author_source": "directory_name",
      "rule_labels": ["greeting", "signature"],
      "llm_decision": "run",
      "llm_reason": "contains social text beyond instrument names",
      "text_fragment_count": 12,
      "useful_fragment_count": 3,
      "parsed_at": "2026-06-23T10:05:00Z"
    }
  ]
}
```

Example `summaries.json`:

```json
{
  "version": 1,
  "updated_at": "2026-06-23T10:20:00Z",
  "items": [
    {
      "module_id": "mod_7e31fd8d54e2",
      "sha256": "8b4d...c1",
      "source_name": "sceneorg-artists",
      "remote_path": "artists/traven/nytrik.mod",
      "model_name": "ministral",
      "prompt_version": "v1",
      "input_text_hash": "31f2...9a",
      "summary_status": "done",
      "summary_error": null,
      "summary_skip_reason": null,
      "summary_path": "data/summaries/sceneorg-artists/artists/traven/nytrik.mod.json",
      "tone": "melancholic",
      "mentions": ["Nytrik"],
      "summarized_at": "2026-06-23T10:20:00Z"
    }
  ]
}
```

## 8. Artifact Naming

Raw file storage should be readable and mirror the source tree as closely as possible.

Format:

- `data/raw_modules/<source_name>/<remote_path>`

Examples:

- `data/raw_modules/sceneorg-artists/zipp/1998/djz_poof.xm`
- `data/raw_modules/sceneorg-artists/artists/traven/nytrik.mod`

The raw filename should stay readable.
The unique identifiers live in JSON state, not in the visible filename.

Required internal identifiers:

- `module_id` = stable internal ID for one source file
- `sha256` = content hash for integrity and duplicate detection

Parsed metadata and summaries should mirror the same readable path:

- `data/parsed_metadata/<source_name>/<remote_path>.json`
- `data/summaries/<source_name>/<remote_path>.json`

## 9. Stage 1: Discovery and Download

Implemented in `scripts/fetch_modules.py`.

### 9.1 Responsibilities

- crawl configured remote roots
- find files matching allowed extensions
- record or update items in `remote_files.json`
- optionally list recent discoveries
- download files with resume support
- compute SHA-256 during download
- preserve a readable local file path
- write files atomically

### 9.2 Source Handling

The first implementation should prefer HTTP directory indexes, because they are simpler to inspect with `requests`.

The crawler only needs to support:

- HTML directory listings with links
- recursive traversal
- filtering by extension

If a source exposes timestamps, they should be stored in `remote_mtime`.
If not, `first_seen_at` becomes the fallback definition of "recent".

### 9.3 Recent File Semantics

The program must support finding recent files in two ways:

- files newly discovered since the previous crawl
- files whose remote timestamp is newer than the stored value

Suggested CLI examples:

```bash
python scripts/fetch_modules.py --discover
python scripts/fetch_modules.py --download
python scripts/fetch_modules.py --recent-days 7
```

### 9.4 Download Behavior

Requirements:

- stream downloads in chunks
- write to `*.part` temporary files first
- rename to final filename only after success
- compute SHA-256 while streaming
- if interrupted, reuse the `.part` file when possible
- if the source does not support byte-range resume, restart that file cleanly

### 9.5 Duplicate Detection

After download:

- compute `sha256`
- store the file under its readable mirrored path
- keep the hash in state for integrity checks
- detect identical content through `sha256`
- do not use the hash as the primary visible filename

## 10. Stage 2: Parsing and Metadata Extraction

Implemented in `scripts/parse_modules.py`.

### 10.1 Responsibilities

- select downloaded module IDs with pending parse status
- extract textual metadata from each module
- separate instrument-like fragments from potentially social text
- assign first-pass rule-based labels
- decide whether the LLM is necessary
- save one normalized JSON file per module
- update `modules.json`

### 10.2 Parsing Strategy

The code should use a format-dispatch approach with plain functions such as:

- `parse_mod(path)`
- `parse_xm(path)`
- `parse_s3m(path)`
- `parse_it(path)`

No parser class hierarchy is required.

### 10.3 Minimum Extraction Contract

Every parsed JSON must contain these fields:

```json
{
  "module_id": "string",
  "sha256": "string",
  "local_path": "string",
  "source_files": [
    {
      "source_name": "sceneorg-artists",
      "remote_path": "artists/traven/file.mod",
      "remote_url": "https://..."
    }
  ],
  "filename": "original file name if known",
  "format": "mod|xm|s3m|it",
  "title": "module title or null",
  "tracker_name": "tracker name or null",
  "author_guess": "best effort author handle or null",
  "author_source": "directory_name|filename|embedded_text|null",
  "sample_names": [],
  "instrument_names": [],
  "song_message": null,
  "text_fragments": [],
  "instrument_like_fragments": [],
  "useful_text_fragments": [],
  "greets_rule_based": [],
  "rule_based_classification": {
    "labels": [],
    "signature_fragments": [],
    "work_offer_fragments": [],
    "contact_fragments": [],
    "technical_fragments": [],
    "llm_decision": "run|skip",
    "llm_reason": "string"
  },
  "parsed_at": "ISO-8601 timestamp"
}
```

### 10.4 Format Expectations

- MOD: title and up to 31 sample names are the main target fields
- XM: title, tracker name, instrument names, sample names, and any available text block
- S3M: title and sample names; extra free-text fields are best effort only
- IT: title, instrument or sample names, and message block if present

The parser does not need to recover every exotic tracker extension in the first version.

### 10.5 Text Normalization

Before sending text to Ollama, the parser should prepare `text_fragments`.

Rules:

- strip null bytes
- trim whitespace
- drop empty strings
- keep original casing
- keep both raw and normalized lists simple
- do not aggressively rewrite the text

### 10.6 Instrument Name Detection

The parser must detect common instrument labels before any LLM call.

This should use a local dictionary from `config/instrument_terms.json`.

Typical terms include:

- `kick`
- `snare`
- `bass`
- `piano`
- `lead`
- `pad`
- `stab`
- `hh`
- `openhh`
- `closedhh`
- `vox`
- `strings`
- `brass`

Detection rules:

- compare on a lowercased and trimmed form
- split common separators such as space, `_`, `-`, `/`, `.`
- allow suffixes such as digits, short qualifiers, and simple FX notes
- keep the rules conservative to avoid classifying social text as an instrument label

Examples that should normally be marked as instrument-like:

- `snare`
- `bass 01`
- `piano-rev`
- `lead a`
- `kickdist`

Examples that should not be auto-classified as instrument-only:

- `call me for music`
- `greets to maze`
- `bassline by traven`
- `need swap with coders`

### 10.7 First Rule-Based Classification

Before any LLM call, the program must assign a first-pass classification from deterministic rules.

The first version should support these labels:

- `instrument_only`
- `greeting`
- `signature`
- `work_offer`
- `contact`
- `technical_note`
- `credits`
- `unknown_social`

Typical rule examples:

- `greeting`: `greets`, `greetz`, `hello`, `hi`, `respect to`
- `signature`: `by <handle>`, `music by`, `coded by`, `made by`
- `work_offer`: `available for`, `looking for group`, `need musician`, `need coder`
- `contact`: `write to`, `call me`, `contact`, `email`
- `technical_note`: `play loud`, `use headphones`, `4 channel`, `stereo`
- `credits`: `samples by`, `inspired by`, `ripped from`

The rule-based layer should be enough to classify many modules without any LLM call.

### 10.8 LLM Eligibility Decision

At the end of parsing, each module must receive `llm_decision = run` or `skip`.

The default must be to skip unless there is a good reason to call the model.

The LLM should be skipped when:

- all extracted text is instrument-like
- there are no `useful_text_fragments`
- the only detected labels are routine labels such as `instrument_only`, `greeting`, `signature`, `work_offer`, `contact`, or `technical_note`
- the useful text is too short to justify inference

The LLM should run when:

- the useful text contains sentence-like social content
- the text is ambiguous after the rule-based pass
- emotional or interpersonal content seems present
- the module contains non-trivial free text that may reveal context, tone, or relations

The parser should store a human-readable `llm_reason` so the decision is easy to audit.

### 10.9 Greeting Extraction

Greeting extraction should start with deterministic rules, not with the LLM alone.

Examples of patterns to detect:

- `greetz to ...`
- `greets to ...`
- `hello to ...`
- `hi to ...`
- comma-separated handle lists after `greets`

The output should be conservative.
If a name is uncertain, it should be omitted rather than guessed.

## 11. Stage 3: LLM Summarization

Implemented in `scripts/run_ollama.py`.

### 11.1 Responsibilities

- select parsed modules marked `llm_decision = run`
- reuse an existing summary when the filtered input text is identical
- build a prompt from useful extracted text only
- call Ollama over HTTP
- write normalized summary JSON
- store summary status and extracted mentions

### 11.2 Ollama Integration

Use the Ollama HTTP API directly with `requests`.
Do not require a wrapper library unless it clearly simplifies the code.

The script must assume Ollama runs locally.

### 11.3 Skip Rules

If a module has no meaningful text:

- do not call Ollama
- create a summary JSON with `summary_status = skipped`
- record the reason

If `llm_decision = skip` in `modules.json`:

- do not call Ollama
- create or update the summary state with `summary_status = skipped`
- set `summary_skip_reason` from the parser decision

### 11.4 LLM Cost Control

The LLM is expected to be slow and should be treated as a selective enrichment step, not as the default parser.

Required controls:

- never send instrument-only fragments
- never send obvious rule-based cases unless explicitly forced
- send only `useful_text_fragments`, not the full raw extracted text
- compute `input_text_hash` on the filtered text and reuse an existing result when the same text was already summarized
- support a CLI flag to force re-run for selected hashes only

### 11.5 Prompt Contract

The prompt must ask for a strict JSON response.

Required response fields:

```json
{
  "summary": "short summary",
  "tone": "one short label",
  "mentions": ["handle1", "handle2"],
  "relationship_notes": ["optional short notes"],
  "confidence": "low|medium|high"
}
```

The prompt version must be stored so summaries can be regenerated later if the prompt changes.
If the configured model or prompt version changes, the existing summary item for that module should be set back to `pending`.

### 11.6 Summary Output Format

Each summary JSON should contain:

```json
{
  "module_id": "string",
  "sha256": "string",
  "summary_status": "done|skipped|failed",
  "summary_skip_reason": "string or null",
  "model_name": "ministral",
  "prompt_version": "v1",
  "input_text_hash": "string",
  "input_text_fragments": [],
  "summary": "string",
  "tone": "string or null",
  "mentions": [],
  "relationship_notes": [],
  "confidence": "string or null",
  "summarized_at": "ISO-8601 timestamp"
}
```

### 11.7 Optional Embeddings-Assisted Classification

Embeddings may help later, but they should not be in the critical path of the first implementation.

Recommended position:

- rule-based filtering first
- optional embeddings second, only for unresolved non-instrument text
- LLM summarization last, only for modules that still justify it

Embeddings are useful for:

- clustering similar social fragments
- suggesting labels for `unknown_social` fragments from already labeled examples
- finding repeated message templates across many modules
- improving search and exploration in the final corpus

Embeddings are not the right first tool for:

- instrument name detection
- simple greeting detection
- obvious signatures
- basic work-offer or contact patterns

If embeddings are added later:

- keep them optional behind `embeddings.enabled`
- do not block the main pipeline when embeddings are disabled
- do not store raw embedding vectors in the main state files
- store only embedding metadata in JSON state, and keep any vector artifacts separate under `data/embeddings/`

## 12. Stage 4: Graph Building

Implemented in `scripts/build_graph.py`.

### 12.1 Responsibilities

- load parsed metadata and summaries
- normalize author and mention handles
- build a directed graph
- export graph data

### 12.2 Graph Rules

- node = a scene handle or author name
- edge direction = author -> mentioned handle
- edge types = `greet` and `mention`
- edge weight = count of repeated occurrences

Rule-based greetings and LLM mentions should stay distinguishable in the exported data.

### 12.3 Exports

Required exports:

- `data/graphs/handles_graph.json`
- `data/graphs/handles_graph.dot`
- `data/graphs/handles_graph.gexf`

The graph build may always rebuild from all current parsed and summarized data.

## 13. Optional Orchestrator

Implemented in `scripts/run_pipeline.py`.

This script should run the stages in order:

1. discovery
2. download
3. parse
4. summarize
5. graph

Suggested CLI examples:

```bash
python scripts/run_pipeline.py
python scripts/run_pipeline.py --skip-summarize
python scripts/run_pipeline.py --recent-days 30
```

The orchestrator should be thin. Most logic must stay in the stage scripts or shared helper functions.

## 14. Logging

Use the standard `logging` module.

Requirements:

- one console logger
- one file logger under `data/logs/`
- each stage logs start, finish, counts, and per-file failures
- errors should include the file hash or remote path when available

No complex log pipeline is required.

## 15. Failure Handling

The code should handle only the common operational failures:

- network timeout
- broken download
- unsupported or corrupt module file
- invalid Ollama response
- missing local artifact file

Handling rules:

- mark the current item as failed
- save the error message in the relevant state file
- continue with the next item

Do not add elaborate retry frameworks.
Simple retry behavior is enough:

- one immediate retry for download
- no automatic infinite retries
- failed items may be retried by rerunning the same script

## 16. Coding Style Requirements

The implementation should follow these rules:

- use `argparse` for CLI parsing
- use `pathlib.Path`
- use `json`
- keep functions short
- avoid nested abstractions
- avoid decorators
- avoid metaprogramming
- avoid complex inheritance
- avoid clever one-liners

Preferred style:

- one file per stage
- one helper module per concern
- explicit JSON load and save functions
- explicit loops
- explicit intermediate variables

## 17. Minimal Acceptance Criteria

The first usable version is complete when it can:

1. crawl at least one configured remote source and record discovered module files
2. download a batch of module files and resume after interruption
3. keep readable raw file paths while storing content hashes for integrity and duplicate detection
4. detect instrument-like text locally and avoid sending it to the LLM
5. classify obvious cases locally such as greetings, signatures, contact, and work offers
6. parse at least title plus embedded textual sample or instrument names where available
7. write one metadata JSON per parsed file
8. summarize only modules with meaningful unresolved text through local Ollama
9. build one directed graph export from the available results
10. rerun safely without redoing already completed work

## 18. Verification Scenarios

The implementation should be checked against these scenarios:

### Scenario A: Interrupted Download

- start downloading a batch
- stop the program manually
- rerun download
- confirm completed files are skipped and incomplete files continue or restart cleanly

### Scenario B: Interrupted Parse

- parse a batch
- stop halfway through
- rerun parse
- confirm only pending or failed files are processed

### Scenario C: Missing Artifact Recovery

- delete one metadata or summary JSON manually
- rerun the relevant stage
- confirm the state item is reset to `pending` and the file is recreated

### Scenario D: New Remote Content

- run discovery once
- run discovery again after new source content appears
- confirm only new or updated files enter the pending download set

### Scenario E: Instrument-Only Module

- parse a module whose extracted text is only instrument labels
- confirm it receives `llm_decision = skip`
- confirm `summaries.json` records `summary_status = skipped`

### Scenario F: Obvious Greeting Without LLM

- parse a module containing `greets to xxx, yyy`
- confirm the greeting targets are extracted by rules
- confirm the module may skip the LLM if there is no richer text

### Scenario G: Duplicate Useful Text

- process two modules with the same filtered useful text
- confirm the second module reuses the first summary instead of calling Ollama again

## 19. Implementation Priority

Recommended order:

1. state and config helpers
2. discovery and download
3. parser for MOD
4. local instrument detection and rule-based classification
5. parser support for XM, S3M, IT
6. Ollama integration with summary reuse
7. graph export
8. thin orchestrator

This order gives a working pipeline early and keeps the first milestone small.
