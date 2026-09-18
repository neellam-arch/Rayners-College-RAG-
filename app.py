import streamlit as st
import os
import uuid
import math
import pymongo
from pypdf import PdfReader
from docx import Document
from google import genai

ADMIN_PIN = os.environ.get("RCL_ADMIN_PIN", "1234")

gemini_client = genai.Client()

EMBEDDING_MODEL = "gemini-embedding-001"

# We tried chromadb (a real vector database) for this, but its
# compiled internals kept crashing the whole app on this machine -
# not something we can fix from our code. A "vector database" is
# really just two things: a place to store each chunk next to its
# embedding, and a way to compare embeddings to find the closest
# match. We started with a local JSON file for that, but a local file
# doesn't survive on free online hosting (the folder gets wiped
# whenever the app restarts or sleeps). MongoDB Atlas is a real,
# free-forever cloud database - the chunks live on Google/Amazon's
# servers instead of this laptop, so they survive no matter what
# happens to the app itself.


@st.cache_resource
def get_chunks_collection():
    """Connects to MongoDB once and reuses that connection on every
    rerun (Streamlit re-runs the whole script on every click, and a
    database connection is too expensive to reopen every single time).
    st.secrets reads values from .streamlit/secrets.toml - a file for
    passwords and connection strings that (unlike normal code) is never
    meant to be shared or committed to GitHub."""
    if "MONGO_URI" not in st.secrets:
        st.error(
            "The document database isn't set up yet. Add your MongoDB "
            "connection string to .streamlit/secrets.toml as MONGO_URI "
            "- see PROJECT_NOTES.md for the setup steps."
        )
        st.stop()
    try:
        mongo_client = pymongo.MongoClient(
            st.secrets["MONGO_URI"], serverSelectionTimeoutMS=5000
        )
        mongo_client.admin.command("ping")  # forces a real connection check now
        database = mongo_client["rcl_knowledge_assistant"]
        return database["chunks"]
    except Exception as e:
        st.error("Couldn't connect to the document database. Double-check the MONGO_URI in .streamlit/secrets.toml. (Technical detail: {})".format(str(e)))
        st.stop()


def embed_texts(texts, task_type):
    """Turns a list of text strings into a list of embeddings (vectors
    of numbers that capture meaning), by calling the Gemini embedding
    API. task_type tells the model whether it's embedding document
    chunks to be stored, or a short question to search with - Gemini
    produces better matches when it knows which one it's doing."""
    response = gemini_client.models.embed_content(
        model=EMBEDDING_MODEL,
        contents=texts,
        config={"task_type": task_type},
    )
    return [embedding.values for embedding in response.embeddings]


def load_chunks():
    """Reads every stored chunk (text + source document + embedding)
    from the database. Returns an empty list if nothing has been
    uploaded yet. The {"_id": 0} part tells MongoDB not to include the
    internal ID it auto-generates for every document - we don't need it."""
    collection = get_chunks_collection()
    return list(collection.find({}, {"_id": 0}))


def add_chunks(chunks):
    """Saves a list of new chunks to the database."""
    if len(chunks) > 0:
        collection = get_chunks_collection()
        collection.insert_many(chunks)


def cosine_similarity(vector_a, vector_b):
    """Measures how similar two embeddings are, from -1 (opposite) to
    1 (identical in meaning). This is the actual "search" math behind
    every vector database - Chroma does the same calculation, just in
    compiled code instead of plain Python."""
    dot_product = sum(a * b for a, b in zip(vector_a, vector_b))
    magnitude_a = math.sqrt(sum(a * a for a in vector_a))
    magnitude_b = math.sqrt(sum(b * b for b in vector_b))
    if magnitude_a == 0 or magnitude_b == 0:
        return 0
    return dot_product / (magnitude_a * magnitude_b)


def chunk_text(text, chunk_size=300, overlap=50):
    """Splits text into overlapping chunks of chunk_size words.
    Overlap means the last `overlap` words of one chunk are repeated
    at the start of the next chunk, so a fact sitting right on a
    boundary still appears whole in at least one chunk."""
    words = text.split()
    chunks = []
    start = 0
    while start < len(words):
        end = start + chunk_size
        chunk_words = words[start:end]
        chunks.append(" ".join(chunk_words))
        start += chunk_size - overlap
    return chunks


def get_document_names():
    """Returns a sorted list of the unique document names stored in
    the database. Each document is split into many chunks, but we only
    want to show its name once in the library list. distinct() is a
    MongoDB feature that returns each different value in a field once,
    instead of us de-duplicating a big list ourselves."""
    collection = get_chunks_collection()
    return sorted(collection.distinct("source"))


def delete_document(filename):
    """Removes every chunk that belongs to one document."""
    collection = get_chunks_collection()
    collection.delete_many({"source": filename})


def search_chunks(question_embedding, top_n=5):
    """Compares the question's embedding against every stored chunk's
    embedding, and returns the top_n most similar chunks - this is the
    "search" step of retrieval."""
    chunks = load_chunks()
    for chunk in chunks:
        chunk["score"] = cosine_similarity(question_embedding, chunk["embedding"])
    chunks.sort(key=lambda c: c["score"], reverse=True)
    return chunks[:top_n]


def ask_gemini_stream(question, context_text):
    """Same job as before (ask Gemini to answer using only the
    retrieved excerpts) but as a generator: instead of waiting for the
    whole answer and returning it in one go, it yields each small piece
    of text (a "delta") the moment Gemini produces it. st.write_stream
    reads a generator like this and prints each piece to the page as
    it arrives - the word-by-word "typing" effect - instead of the
    page sitting frozen until the full answer is ready."""
    context_text = context_text[:12000]
    prompt = (
        "Answer the question using ONLY the information in the excerpts below. "
        "If the excerpts don't contain the answer, say so clearly.\n\n"
        "Excerpts:\n" + context_text + "\n\n"
        "Question: " + question
    )
    try:
        stream = gemini_client.interactions.create(
            model="gemini-3.8-flash",
            input=prompt,
            stream=True,
        )
        for event in stream:
            if event.event_type == "step.delta" and event.delta.type == "text":
                yield event.delta.text
            elif event.event_type == "error":
                yield "\n\n⚠️ " + event.error.message
                return
    except Exception as e:
        yield "Something went wrong talking to the AI. If you've been testing a lot in a short time, you may have hit the free-tier rate limit — wait about a minute and try again. (Technical detail: {})".format(str(e))

st.set_page_config(
    page_title="RCL Knowledge Assistant",
    page_icon="assets/rcl_logo.png",
    layout="wide",
)

# This CSS block does three jobs: (1) loads a cleaner Google font,
# (2) keeps the page content a comfortable reading width even though
# we're using Streamlit's "wide" layout, and (3) restyles buttons and
# inputs on top of the blue/white theme set in .streamlit/config.toml
CUSTOM_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');

html, body, [class*="css"] {
    font-family: 'Inter', sans-serif;
}

.block-container {
    max-width: 880px;
    padding-top: 2.5rem;
    padding-bottom: 3rem;
    margin: 0 auto;
}

.rcl-header {
    background: linear-gradient(135deg, #1B4F91 0%, #2E73C2 100%);
    color: white;
    padding: 2rem 2.2rem;
    border-radius: 16px;
    margin-bottom: 2rem;
    box-shadow: 0 8px 24px rgba(27, 79, 145, 0.18);
}
.rcl-header h1 {
    margin: 0;
    font-size: 1.9rem;
    font-weight: 700;
}
.rcl-header p {
    margin: 0.4rem 0 0 0;
    font-size: 1.02rem;
    opacity: 0.92;
    font-weight: 400;
}

.stButton > button {
    border-radius: 10px;
    border: none;
    background-color: #1B4F91;
    color: white;
    font-weight: 600;
    padding: 0.5em 1.4em;
    transition: transform 0.15s ease, box-shadow 0.15s ease;
}
.stButton > button:hover {
    background-color: #123B6E;
    color: white;
    transform: translateY(-2px);
    box-shadow: 0 4px 12px rgba(27, 79, 145, 0.35);
}
div[data-testid="stTextInput"] input {
    border-radius: 10px;
}

section[data-testid="stSidebar"] .stRadio label {
    font-size: 1.02rem;
    padding: 0.25rem 0;
}
</style>
"""
st.markdown(CUSTOM_CSS, unsafe_allow_html=True)

# st.logo puts the logo in one fixed spot (top-left, above the sidebar
# nav) - it should only be called once, otherwise it looks duplicated.
st.logo("assets/rcl_logo.png", size="large")


def page_header(title, subtitle):
    """Shows a blue gradient banner with a title and subtitle.
    Used at the top of each page instead of a plain st.title()."""
    st.markdown(
        '<div class="rcl-header"><h1>' + title + "</h1><p>" + subtitle + "</p></div>",
        unsafe_allow_html=True,
    )


if "unlocked" not in st.session_state:
    st.session_state.unlocked = False

st.sidebar.title("RCL Knowledge Assistant")

# format_func lets us show a nicer label (with an icon) in the sidebar,
# while the actual value stored in `page` stays the plain string below -
# so the rest of the code doesn't need to change at all.
NAV_ICONS = {
    "Ask a Question": "📚",
    "Admin Upload": "🛠️",
}


def with_icon(label):
    return NAV_ICONS[label] + "  " + label


page = st.sidebar.radio(
    "Navigate", ["Ask a Question", "Admin Upload"], format_func=with_icon
)
st.sidebar.markdown("---")
st.sidebar.caption("Rayners College London · Internal knowledge tool")

if page == "Ask a Question":
    page_header(
        "Ask a Question",
        "Get instant answers from RCL's official course and admissions documents.",
    )
    question = st.text_input("Type your question below", placeholder="e.g. What are the entry requirements for the Business Foundation course?")

    if st.button("🔍 Ask"):
        if len(load_chunks()) == 0:
            st.warning("No documents uploaded yet. Ask an admin to upload something first.")
        else:
            # Step 1: search - turn the question into an embedding and
            # compare it against every stored chunk's embedding to find
            # the closest matches, across every document in the library.
            try:
                with st.spinner("Searching documents..."):
                    question_embedding = embed_texts([question], "RETRIEVAL_QUERY")[0]
                    top_chunks = search_chunks(question_embedding, top_n=5)
            except Exception as e:
                st.error("Couldn't search the documents right now - if you've been testing a lot in a short time, you may have hit the free-tier rate limit. Wait about a minute and try again. (Technical detail: {})".format(str(e)))
                st.stop()

            retrieved_chunks = [c["text"] for c in top_chunks]
            retrieved_sources = [c["source"] for c in top_chunks]
            context_text = "\n\n".join(retrieved_chunks)

            # Step 2: answer - only the retrieved chunks (not the whole
            # library) are sent to Gemini, and the answer streams onto
            # the page piece by piece instead of appearing all at once.
            st.markdown("##### Answer")
            with st.container(border=True):
                st.write_stream(ask_gemini_stream(question, context_text))

            with st.expander("📎 Sources used for this answer"):
                for source, chunk in zip(retrieved_sources, retrieved_chunks):
                    st.markdown("**" + source + "**")
                    preview = chunk[:300] + ("..." if len(chunk) > 300 else "")
                    st.caption(preview)

else:
    page_header(
        "Admin Upload",
        "Manage the documents the assistant is allowed to answer questions from.",
    )

    if not st.session_state.unlocked:
        pin_attempt = st.text_input("Enter admin PIN", type="password")
        if st.button("🔓 Unlock"):
            if pin_attempt == ADMIN_PIN:
                st.session_state.unlocked = True
                st.rerun()
            else:
                st.error("Wrong PIN.")

    else:
        st.success("Unlocked!")

        st.subheader("📄 Upload a new document")
        uploaded_file = st.file_uploader(
            "Choose a file", type=["pdf", "docx", "txt", "png", "jpg", "jpeg"]
        )

        extracted_text = ""

        if uploaded_file is not None:
            filename = uploaded_file.name.lower()

            if filename.endswith(".txt"):
                extracted_text = uploaded_file.read().decode("utf-8")
            elif filename.endswith(".pdf"):
                reader = PdfReader(uploaded_file)
                for page_obj in reader.pages:
                    extracted_text += page_obj.extract_text() or ""
            elif filename.endswith(".docx"):
                doc = Document(uploaded_file)
                for paragraph in doc.paragraphs:
                    extracted_text += paragraph.text + "\n"
            elif filename.endswith((".png", ".jpg", ".jpeg")):
                st.image(uploaded_file)
                extracted_text = "[Image file - text extraction comes later]"

            st.subheader("Preview:")
            st.write(extracted_text)

            if st.button("➕ Add to library"):
                text_chunks = chunk_text(extracted_text)
                try:
                    # One call embeds every chunk from this document
                    # together, instead of one API call per chunk -
                    # much friendlier to the free-tier rate limit.
                    with st.spinner("Embedding and storing chunks..."):
                        embeddings = embed_texts(text_chunks, "RETRIEVAL_DOCUMENT")
                        new_chunks = []
                        for text_chunk, embedding in zip(text_chunks, embeddings):
                            new_chunks.append({
                                "id": str(uuid.uuid4()),
                                "source": uploaded_file.name,
                                "text": text_chunk,
                                "embedding": embedding,
                            })
                        add_chunks(new_chunks)
                    st.success("Added " + uploaded_file.name + " (" + str(len(text_chunks)) + " chunks)")
                    st.rerun()
                except Exception as e:
                    st.error("Couldn't store this document right now - if you've been testing a lot in a short time, you may have hit the free-tier rate limit. Wait about a minute and try again. (Technical detail: {})".format(str(e)))

        st.divider()
        st.subheader("📚 Documents in the library")

        doc_names = get_document_names()

        if len(doc_names) == 0:
            st.write("Nothing uploaded yet.")
        else:
            for name in doc_names:
                with st.container(border=True):
                    col1, col2 = st.columns([4, 1])
                    with col1:
                        st.write("📄 " + name)
                    with col2:
                        if st.button("🗑️ Delete", key="delete_" + name):
                            delete_document(name)
                            st.rerun()