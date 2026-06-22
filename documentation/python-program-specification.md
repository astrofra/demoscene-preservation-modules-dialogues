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
- incremental download and deduplication
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

The program should be a filesystem-based pipeline with one small SQLite database used as the central state store.

Reason for SQLite:

- it is included with Python
- it avoids many fragile JSON state files
- it makes resume and incremental processing simple
- it is still easy to inspect manually

The core rule is:

- raw files and generated artifacts live in `data/`
- pipeline state lives in one SQLite database
- every stage reads pending work from the database and writes its results back to the database

## 5. Repository Layout

The implementation should use this structure:

```text
MODialogues/
|
+-- config/
|   +-- config.json
|
+-- data/
|   +-- raw_modules/
|   |   +-- mod/
|   |   +-- xm/
|   |   +-- s3m/
|   |   +-- it/
|   +-- parsed_metadata/
|   +-- summaries/
|   +-- graphs/
|   +-- logs/
|   +-- state/
|       +-- pipeline.db
|
+-- scripts/
|   +-- fetch_modules.py
|   +-- parse_modules.py
|   +-- run_ollama.py
|   +-- build_graph.py
|   +-- run_pipeline.py
|   +-- common_db.py
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
  "ollama": {
    "base_url": "http://127.0.0.1:11434",
    "model": "ministral",
    "embedding_model": "qwen3-embedding",
    "timeout_seconds": 120
  },
  "paths": {
    "database": "data/state/pipeline.db"
  }
}
```

TOML should be avoided because Python 3.9 does not include a built-in TOML reader.

## 7. Pipeline State Model

The SQLite database is the source of truth for progress tracking.

### 7.1 Required Tables

#### `remote_files`

Tracks discovered files from remote sources.

Required columns:

- `id`
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

#### `modules`

Tracks parsing results for unique downloaded file contents.

Required columns:

- `id`
- `sha256`
- `format`
- `parse_status` (`pending`, `done`, `failed`, `skipped`)
- `parse_error`
- `metadata_path`
- `title`
- `tracker_name`
- `author_guess`
- `author_source`
- `parsed_at`

Unique key:

- `sha256`

#### `summaries`

Tracks LLM output.

Required columns:

- `id`
- `module_id`
- `model_name`
- `prompt_version`
- `summary_status` (`pending`, `done`, `failed`, `skipped`)
- `summary_error`
- `summary_path`
- `tone`
- `mentions_json`
- `summarized_at`

### 7.2 Resume Rules

The pipeline is resumable because each stage only selects records with pending or failed work.

Rules:

- discovery can be rerun at any time
- download skips rows already marked `done` if the target file still exists
- parse skips hashes already marked `done` if the metadata JSON still exists
- summarization skips rows already marked `done` if the summary JSON still exists
- graph building may rebuild from scratch each time because it is derived output

If a database row says `done` but the corresponding artifact file is missing, the stage must reset that row to `pending` and recreate the artifact.

## 8. Artifact Naming

Canonical raw file storage should be hash-based.

Format:

- `data/raw_modules/mod/<sha256>.mod`
- `data/raw_modules/xm/<sha256>.xm`
- `data/raw_modules/s3m/<sha256>.s3m`
- `data/raw_modules/it/<sha256>.it`

This avoids duplicate content from multiple mirrors or duplicate filenames.

Parsed metadata and summaries should also be keyed by hash:

- `data/parsed_metadata/<sha256>.json`
- `data/summaries/<sha256>.json`

## 9. Stage 1: Discovery and Download

Implemented in `scripts/fetch_modules.py`.

### 9.1 Responsibilities

- crawl configured remote roots
- find files matching allowed extensions
- record or update entries in `remote_files`
- optionally list recent discoveries
- download files with resume support
- compute SHA-256 during download
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

### 9.5 Deduplication

After download:

- compute `sha256`
- store the file under the canonical hash-based path
- if another remote file already has the same hash, do not keep a second raw copy
- still keep both `remote_files` rows so provenance is preserved

## 10. Stage 2: Parsing and Metadata Extraction

Implemented in `scripts/parse_modules.py`.

### 10.1 Responsibilities

- select unique downloaded hashes with pending parse status
- extract textual metadata from each module
- save one normalized JSON file per module
- update the `modules` table

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
  "sha256": "string",
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
  "greets_rule_based": [],
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

### 10.6 Greeting Extraction

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

- select parsed modules with pending summary status
- build a prompt from extracted text
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

### 11.4 Prompt Contract

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

### 11.5 Summary Output Format

Each summary JSON should contain:

```json
{
  "sha256": "string",
  "summary_status": "done|skipped|failed",
  "model_name": "ministral",
  "prompt_version": "v1",
  "input_text_fragments": [],
  "summary": "string",
  "tone": "string or null",
  "mentions": [],
  "relationship_notes": [],
  "confidence": "string or null",
  "summarized_at": "ISO-8601 timestamp"
}
```

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
- save the error message in the database
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
- use `sqlite3`
- keep functions short
- avoid nested abstractions
- avoid decorators
- avoid metaprogramming
- avoid complex inheritance
- avoid clever one-liners

Preferred style:

- one file per stage
- one helper module per concern
- explicit SQL statements
- explicit loops
- explicit intermediate variables

## 17. Minimal Acceptance Criteria

The first usable version is complete when it can:

1. crawl at least one configured remote source and record discovered module files
2. download a batch of module files and resume after interruption
3. deduplicate identical files by content hash
4. parse at least title plus embedded textual sample or instrument names where available
5. write one metadata JSON per parsed file
6. summarize files with meaningful text through local Ollama
7. build one directed graph export from the available results
8. rerun safely without redoing already completed work

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
- confirm the database entry is reset to `pending` and the file is recreated

### Scenario D: New Remote Content

- run discovery once
- run discovery again after new source content appears
- confirm only new or updated files enter the pending download set

## 19. Implementation Priority

Recommended order:

1. database and config helpers
2. discovery and download
3. parser for MOD
4. parser support for XM, S3M, IT
5. Ollama integration
6. graph export
7. thin orchestrator

This order gives a working pipeline early and keeps the first milestone small.
