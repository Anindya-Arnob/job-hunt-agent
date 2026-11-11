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
