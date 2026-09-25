# Vault frontend

From this folder:

```bat
python -m pip install -r frontend/requirements.txt
python -m streamlit run frontend/app.py
```

Open http://localhost:8501. Keep your existing recovery service running on port 8003. File upload/download/delete require the separate main storage backend, normally port 8000.

See `frontend/README.md` for full Windows instructions, `API_CONTRACT.md` for your teammate's backend requirements, and `VALIDATION.md` for test results.

The app starts in Live services mode. Design preview is an explicitly labeled, read-only sidebar option.
