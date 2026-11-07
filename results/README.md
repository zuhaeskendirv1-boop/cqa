Official submission files (CQA Student Guide Appendix, JSON schema 1.0):

- `dev/results.json`  — DEV set (60 chats + 5 calls)
- `prod/results.json` — 10 control chats

Generate from the UI (Диалоги → «Записать results/…») or:

    python tools/export_results.py --group DEV --target dev
    python tools/export_results.py --group <control group_id> --target prod

Then: `git add results && git commit -m "results" && git push`
