# MODialogues

**MODialogues** is an experimental archival and analytical project that explores the hidden social fabric of the demoscene through tracker music modules (MOD/XM/S3M/IT). It aims to extract, index, and analyze embedded messages left by musicians in instrument/sample names and song messages—often overlooked textual artifacts that reflect greetings, personal notes, frustrations, and emotional traces from a pre-social media era.

---

## ✨ Project Goals

- Mirror/download a large dataset of ProTracker (MOD), FastTracker (XM), ScreamTracker (S3M), and Impulse Tracker (IT) music modules.
- Extract embedded text content (instrument/sample names, song messages).
- Classify and group modules by artist/handle.
- Use local LLM (via [Ollama](https://ollama.ai/)) to:
  - Summarize message tone, content, sentiment.
  - Detect inferred relationships between scene members.
- Map "greets" and mentions into a directed social graph.
- Provide a navigable timeline and interface for exploring the findings.

---

## 📦 Folder Structure

```
MODialogues/
│
├── data/
│   ├── raw_modules/         # All raw .mod, .xm, .s3m, .it files (downloaded)
│   ├── parsed_metadata/     # JSON files with extracted metadata per module
│   ├── summaries/           # LLM-generated summaries of each module’s textual content
│   └── graphs/              # Relationship graphs (DOT, JSON, etc.)
│
├── scripts/
│   ├── fetch_modules.py     # Downloads modules from ftp.scene.org or other mirrors
│   ├── parse_modules.py     # Extracts metadata and text fields
│   ├── run_ollama.py        # Feeds extracted texts into local LLM via Ollama
│   └── build_graph.py       # Builds social graph based on greets and messages
│
├── web-ui/                  # Optional frontend for exploration
│
└── README.md
```

---

## 🚧 Roadmap

### Phase 1: Acquisition

- [ ] Write `fetch_modules.py`
  - Mirror selected directories from:
    - `ftp.scene.org/pub/mod/`
    - Optional: `modarchive.org` (respecting API limits)
  - Organize downloads by filetype (`.mod`, `.xm`, etc.)
  - Avoid duplicates (e.g., MD5 hashes)

---

### Phase 2: Parsing & Metadata Extraction

- [ ] Write `parse_modules.py`:
  - Use [`py-fasttracker`](https://pypi.org/project/py-fasttracker/) or write a low-level parser
  - Extract:
    - Module title
    - Tracker name
    - Sample/instrument names
    - Song message (if available)
    - Author handle (best-effort)
  - Store structured output as JSON:
    ```json
    {
      "filename": "traven-nytrik.mod",
      "tracker": "ProTracker 2.3",
      "title": "Why not call?",
      "author": "Traven",
      "sample_names": [
        "Nytrik is in Paris...",
        "Why doesn't he call?",
        "kick",
        "snare"
      ],
      "song_message": null,
      "greets": ["Nytrik"]
    }
    ```

---

### Phase 3: LLM-Based Summarization

- [ ] Write `run_ollama.py`:
  - Use local Ollama (with `mistral`, `llama3`, or similar lightweight model)
  - Feed sample names and messages with a prompt like:
    > "This is a text extracted from a 1990s MOD tracker music file. Please summarize the emotional tone, intention, and probable context of these messages."

  - Output example:
    ```json
    {
      "summary": "The author expresses a sense of disappointment and longing toward a fellow musician named Nytrik.",
      "tone": "melancholic",
      "mentions": ["Nytrik"]
    }
    ```

---

### Phase 4: Graph Building

- [ ] Write `build_graph.py`:
  - Construct a directed graph of greets/mentions using `networkx`
  - Nodes = authors / handles
  - Edges = greet/mention direction (with weights if repeated)
  - Export:
    - `.dot`
    - `.json`
    - `.gexf` (for Gephi)

---

### Phase 5 (Optional): Interface / Visualization

- [ ] Build a web UI with:
  - Time slider (years)
  - Author list / search
  - Message overlay + player
  - Interactive social graph

---

## 🧠 Dependencies (Python 3.9+)

```bash
pip install requests tqdm networkx ollama fasttracker-parser
```

LLM usage (local):
```bash
ollama run mistral
```

---

## 📄 License

MIT License. This project respects the copyright and artistic rights of original
tracker musicians. This is a non-commercial, archival, and research-oriented project.

---

## 💡 Inspiration

This project was inspired by a Japanese archivist's blog post exploring poetic messages left in MOD files and a quote from a Protracker module by **Traven**:

> “Nytrik est sur Paris, pourquoi il m'appelle pas ?”  
> *—Traven, ca. pre-Facebook era*
