"""Optional grounded mode: chunk, embed, and retrieve study material (RAG).

I keep heavy imports inside methods so Leo still runs without the RAG extras.
"""
import os


class KnowledgeBase:
    def __init__(self) -> None:
        self._store = None
        self.sources: list[str] = []

    def add_text(self, text: str, source: str) -> int:
        """Split text into 400-character chunks, embed them, and index them in FAISS."""
        from langchain_community.vectorstores import FAISS
        from langchain_core.documents import Document
        from langchain_google_genai import GoogleGenerativeAIEmbeddings
        from langchain_text_splitters import RecursiveCharacterTextSplitter

        splitter = RecursiveCharacterTextSplitter(chunk_size=400, chunk_overlap=50)
        chunks = splitter.split_documents([Document(page_content=text, metadata={"source": source})])
        if not chunks:
            return 0
        if self._store is None:
            emb = GoogleGenerativeAIEmbeddings(model="models/gemini-embedding-001",
                                               google_api_key=os.getenv("GEMINI_API_KEY"))
            self._store = FAISS.from_documents(chunks, emb)
        else:
            self._store.add_documents(chunks)
        self.sources.append(source)
        return len(chunks)

    def retrieve(self, query: str, k: int = 4, source: str | None = None) -> str:
        """Similarity search with an optional metadata filter on the source name."""
        if self._store is None:
            return ""
        extra = {"filter": {"source": source}} if source else {}
        docs = self._store.similarity_search(query, k=k, **extra)
        return "\n---\n".join(d.page_content for d in docs)
