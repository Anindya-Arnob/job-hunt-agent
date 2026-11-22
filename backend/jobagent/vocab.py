"""Skill vocabulary and synonyms used by the offline heuristics and the honesty checks."""
from __future__ import annotations

import re

SKILLS = [
    # languages & data
    "Python", "SQL", "R", "Java", "Scala", "C++", "C#", "Go", "Rust", "JavaScript", "TypeScript", "Bash",
    "PostgreSQL", "MySQL", "MongoDB", "MongoDB Atlas", "Supabase", "NoSQL", "Snowflake", "BigQuery", "Redshift",
    "Databricks", "Spark", "PySpark", "Hadoop", "Kafka", "Airflow", "dbt", "ETL", "ELT", "Data Warehouse",
    "Data Lake", "Delta Lake", "Pandas", "NumPy", "Polars", "Excel", "Power BI", "Tableau", "Looker", "Matplotlib",
    "Seaborn", "Plotly", "Streamlit", "Jupyter",
    # ML
    "Machine Learning", "Deep Learning", "Scikit-learn", "TensorFlow", "Keras", "PyTorch", "XGBoost", "LightGBM",
    "Statistics", "Regression", "Classification", "Clustering", "Time-Series Forecasting", "Feature Engineering",
    "A/B Testing", "Computer Vision", "OpenCV", "CNN", "RNN", "LSTM", "GRU", "Transformers", "Attention Mechanism",
    "NLP", "spaCy", "NLTK", "BERT", "DistilBERT", "RoBERTa", "Flan-T5", "BERTopic", "Hugging Face",
    "Hugging Face Transformers", "Tokenization", "Embeddings", "Vector Embeddings", "Vector Databases",
    "Semantic Search", "RAG", "Prompt Engineering", "Fine-Tuning", "LoRA", "PEFT", "QLoRA", "Quantization",
    "bitsandbytes", "LLM", "LLMs", "Large Language Models", "Generative AI", "GenAI", "Diffusion Models",
    "Stable Diffusion", "FLUX", "ComfyUI", "LangChain", "LangGraph", "LlamaIndex", "AI Agents", "Agentic",
    "OpenAI", "GPT", "Claude", "Llama", "Mistral", "vLLM", "Ollama", "FAISS", "Pinecone", "Chroma", "Weaviate",
    "Qdrant", "Elasticsearch", "MLflow", "Weights & Biases", "Kubeflow", "MLOps", "LLMOps", "Model Evaluation",
    "Model Deployment", "Model Monitoring",
    # engineering
    "Flask", "FastAPI", "Django", "REST APIs", "REST", "GraphQL", "Docker", "Kubernetes", "Terraform", "CI/CD",
    "GitHub Actions", "Git", "GitHub", "Linux", "AWS", "SageMaker", "Azure", "Azure ML", "GCP",
    "Google Cloud Platform", "Vertex AI", "Google Colab", "n8n", "Claude Code",
]

# Different words, same skill: resume term -> how postings often write it
SYNONYMS = {
    "Scikit-learn": ["sklearn", "scikit learn"],
    "Hugging Face Transformers": ["huggingface", "hugging face", "transformers library"],
    "Large Language Models": ["LLM", "LLMs"],
    "LLM": ["Large Language Models", "LLMs"],
    "Google Cloud Platform": ["GCP", "Google Cloud"],
    "GCP": ["Google Cloud Platform", "Google Cloud"],
    "REST APIs": ["REST", "RESTful APIs", "APIs"],
    "Fine-Tuning (LoRA / PEFT)": ["fine-tuning", "finetuning", "LoRA", "PEFT"],
    "Vector Embeddings": ["embeddings", "vector search"],
    "Vector Databases": ["vector database", "vector store", "vector DB"],
    "RAG": ["Retrieval-Augmented Generation", "retrieval augmented generation"],
    "Generative AI": ["GenAI", "Gen AI"],
    "Time-Series Forecasting": ["time series", "forecasting"],
    "Model Evaluation & Validation": ["model evaluation", "evaluation"],
    "MS Office (Excel)": ["Excel", "Microsoft Excel", "MS Excel"],
    "Docker": ["containerization", "containers"],
    "Git": ["version control"],
    "Pandas": ["pandas"],
    "NLP": ["Natural Language Processing"],
}

# Skills postings ask for in words, often German: one name (shown, and written on an English CV) -> every way
# an ad or a CV writes it. "Fehlerbehandlung" in a German ad and "error handling" on an English CV are the
# same keyword, both when the ad is read and when the CV is checked.
CONCEPTS = {
    "Automated Testing": ["automated testing", "automated tests", "automatisierte Tests", "automatisierten Tests",
                          "automatisiertes Testen", "Testautomatisierung", "test automation", "unit tests",
                          "unit testing", "Unit-Tests", "integration tests", "pytest", "test-driven development", "TDD"],
    "Tool Calling": ["tool calling", "tool calls", "function calling", "tool use"],
    "LLM Orchestration": ["LLM orchestration", "LLM-Orchestrierung", "orchestration of LLMs", "LangGraph",
                          "agent orchestration"],
    "Agentic Workflows": ["agentic workflows", "agentic workflow", "agentische Workflows", "agentischen Workflows",
                          "AI agents", "KI-Agenten", "multi-agent", "Agentic Workflow Automation"],
    "Error Handling": ["error handling", "Fehlerbehandlung", "exception handling", "retries", "retry logic",
                       "fault tolerance", "Fehlertoleranz", "fallbacks"],
    "Monitoring": ["monitoring", "observability", "Überwachung", "alerting", "progress monitoring", "run logs"],
    "Security": ["security", "Sicherheit", "IT-Sicherheit", "Datensicherheit", "secure coding", "authentication"],
    "Cost Control": ["cost control", "Kostenkontrolle", "cost optimization", "Kostenoptimierung", "budget cap",
                     "token tracking", "token budget"],
    "Performance": ["performance", "performance optimization", "Performanceoptimierung", "Performance-Optimierung", "latency",
                    "Latenz", "performance tuning"],
    "Backend Development": ["backend", "back-end", "Backend-Services", "backend services", "Backend-Entwicklung",
                            "backend development", "server-side"],
    "APIs": ["APIs", "API", "REST APIs", "RESTful APIs", "REST API", "Schnittstellen", "API integration",
             "FastAPI"],
    "Integrations": ["integrations", "Integrationen", "system integration", "API integration"],
    "Software Engineering": ["software engineering", "Softwareentwicklung", "software development",
                             "Software-Engineering", "Software-Engineering-Praktiken"],
    "Customer Collaboration": ["customer collaboration", "Zusammenarbeit mit Kunden", "Kundenkontakt",
                               "client-facing", "customer-facing", "stakeholder management", "Kundenprojekte",
                               "working with customers", "working with clients"],
    "Business Process Analysis": ["business process analysis", "business processes", "Geschäftsprozesse",
                                  "Geschäftsprozessen", "Prozessanalyse", "process analysis", "requirements analysis",
                                  "Anforderungsanalyse"],
    "Data Structures": ["data structures", "Datenstrukturen", "data modeling", "Datenmodellierung"],
    "Production Deployment": ["production", "in production", "produktiven Einsatz", "Produktivbetrieb",
                              "production-grade", "produktionsreif", "produktionsreife", "produktionsreifen",
                              "deployment", "deployed"],
    "Artificial Intelligence": ["artificial intelligence", "KI", "künstliche Intelligenz", "AI"],
    "Machine Learning": ["machine learning", "maschinelles Lernen", "ML"],
    "Data Analysis": ["data analysis", "Datenanalyse", "data analytics", "Datenauswertung"],
    "Agile": ["agile", "Scrum", "Kanban", "agil", "agilen", "agiles Arbeiten"],
    "Communication Skills": ["communication skills", "Kommunikationsfähigkeit", "Kommunikationsstärke",
                             "kommunikationsstark"],
    "Analytical Skills": ["analytical skills", "Analysefähigkeit", "analytische Fähigkeiten", "analytisches Denken",
                          "strukturierte Analysefähigkeit"],
    "Teamwork": ["teamwork", "team player", "Teamfähigkeit", "Teamplayer"],
    "Documentation": ["documentation", "Dokumentation", "technical documentation"],
    "Microservices": ["microservices", "Microservice-Architektur"],
    "Cloud Computing": ["cloud computing", "cloud", "Cloud-Plattformen"],
}
_CONCEPT_OF = {}
for _c, _vs in CONCEPTS.items():
    for _v in [_c, *_vs]:
        _CONCEPT_OF.setdefault(_v.lower(), _c)


def concept_of(term: str) -> str:
    """The shared name of a concept ('Fehlerbehandlung' -> 'Error Handling'), or '' when it is not one."""
    return _CONCEPT_OF.get((term or "").lower(), "")


_SKILL_SET = {x.lower() for x in SKILLS}


def alternatives(term: str) -> list[str]:
    """'Gurobi or CPLEX or OR-Tools' (the ad wants one of them) -> each name; any other term -> itself."""
    if " or " not in (term or ""):
        return [term]
    return [p.strip() for p in term.split(" or ") if p.strip()]


def variants(term: str) -> list[str]:
    """Every way of writing this keyword (the term itself first). A tool asked for by name (LangGraph, FastAPI,
    pytest) stays itself: only the concept's own name stands for all of its forms."""
    lg = _LEARNED_OF.get((term or "").lower())
    if lg:                                       # a learned word: 'OR-Tools' = 'Google OR-Tools'
        return list(dict.fromkeys([term, lg, *LEARNED[lg]]))
    c = concept_of(term)
    if not c or (term.lower() != c.lower() and term.lower() in _SKILL_SET):
        return [term]
    return list(dict.fromkeys([term, c, *CONCEPTS[c]]))


# Ways of working, not tools: on a CV they belong in a bullet that shows them ("Added retries and fallbacks"),
# not in the skills list, where "Cost Control · Performance · Integrations" reads like keyword stuffing.
PRACTICES = {"Error Handling", "Monitoring", "Security", "Cost Control", "Performance", "Integrations",
             "Customer Collaboration", "Business Process Analysis", "Production Deployment", "Communication Skills",
             "Analytical Skills", "Teamwork", "Documentation", "Software Engineering", "Data Structures"}


def is_practice(term: str) -> bool:
    c = concept_of(term)
    return bool(c) and c in PRACTICES and term.lower() not in _SKILL_SET


def concept_key(term: str) -> str:
    """One key per keyword: the concept for its forms, the tool itself for tools."""
    lg = _LEARNED_OF.get((term or "").lower())
    if lg:
        return lg.lower()
    c = concept_of(term)
    if not c or (term.lower() != c.lower() and term.lower() in _SKILL_SET):
        return term.lower()
    return c.lower()


def find_concepts(text: str) -> list[str]:
    """Concepts an ad (or a CV) names in any of their forms. Short forms (AI, KI, ML, API) only count with
    their capitals, so 'ai' or 'ml' inside other words or URLs do not."""
    out = []
    for c, vs in CONCEPTS.items():
        for v in [c, *vs]:
            if len(v) <= 3:
                if re.search(rf"(?<![A-Za-z0-9+#]){re.escape(v)}(?![A-Za-z0-9+#])", text or ""):
                    out.append(c)
                    break
            elif term_regex(v).search(text or ""):
                out.append(c)
                break
    return out


ALL_TERMS = list(dict.fromkeys(SKILLS + [a for alts in SYNONYMS.values() for a in alts
                                         if a not in ("APIs", "evaluation", "containers", "pandas")]))

_word_cache: dict[str, re.Pattern] = {}


def term_regex(term: str) -> re.Pattern:
    if term not in _word_cache:
        esc = re.escape(term)
        _word_cache[term] = re.compile(rf"(?<![A-Za-z0-9+#]){esc}(?![A-Za-z0-9+#])", re.I)
    return _word_cache[term]


def find_terms(text: str, terms=None) -> list[str]:
    terms = terms or SKILLS
    out = []
    for t in terms:
        if term_regex(t).search(text or ""):
            out.append(t)
    # prefer the longer term when both match (e.g. "MongoDB Atlas" over "MongoDB")
    return [t for t in out if not any(o != t and t.lower() in o.lower() for o in out)]


def norm_tool(s: str) -> str:
    s = s.lower().strip()
    s = re.sub(r"\(.*?\)", "", s)
    return re.sub(r"[^a-z0-9+#]+", "", s)


_SHORT_CTX = re.compile(r"(?= (language|lang|code|programming|developer|scripts?|packages?|studio|\d))", re.I)


def find_terms_strict(text: str, terms=None) -> list[str]:
    """find_terms, but short names (R, Go, C, Rust...) only count when written with their capital and,
    for the one- and two-letter ones, used like a language ("R scripts", "Go code", "C++")."""
    text = text or ""
    out = []
    for t in find_terms(text, terms):
        if len(t) <= 4 and not re.search(rf"(?<![A-Za-z0-9+#]){re.escape(t)}(?![A-Za-z0-9+#])", text):
            continue                                   # 'go' / 'r' in normal words
        if len(t) <= 2 and t.isalpha():
            ok = any(_SHORT_CTX.match(text, m.end()) or re.search(r"(,|/|und|and)\s*$", text[max(0, m.start() - 6):m.start()])
                     for m in re.finditer(rf"(?<![A-Za-z0-9+#]){re.escape(t)}(?![A-Za-z0-9+#])", text))
            if not ok:
                continue
        out.append(t)
    return out


# ------------------------------------------------------------- learned words ---
# Skill and tool names Claude found in ads you built a CV for, with their other spellings. The free word matcher
# uses them from then on, so the next ad that asks for them is scored right without spending tokens.
# Kept in the app's data (Settings -> Learned words), loaded at start; built-in words are never touched.
LEARNED: dict[str, list[str]] = {}
_LEARNED_OF: dict[str, str] = {}


def known(term: str) -> bool:
    t = (term or "").lower()
    return t in _SKILL_SET or t in {x.lower() for x in ALL_TERMS} or bool(concept_of(term)) or t in _LEARNED_OF


def set_learned(words: dict[str, list[str]]) -> None:
    """Replace the learned words (in place, so every module that imported the lists sees them)."""
    old = {w for k, alts in LEARNED.items() for w in [k, *alts]}
    SKILLS[:] = [x for x in SKILLS if x not in old]
    ALL_TERMS[:] = [x for x in ALL_TERMS if x not in old]
    LEARNED.clear()
    _LEARNED_OF.clear()
    for k, alts in words.items():
        if not k or k.lower() in _SKILL_SET:
            continue
        alts = [a for a in alts if a and a.lower() != k.lower()]
        LEARNED[k] = alts
        SKILLS.append(k)
        ALL_TERMS.append(k)
        ALL_TERMS.extend(a for a in alts if len(a) > 2 and a not in ALL_TERMS)   # 'LP' alone: too many false hits
        for f in [k, *alts]:
            _LEARNED_OF.setdefault(f.lower(), k)
