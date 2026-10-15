#!/usr/bin/env bash
# Run the production verification. Kept as a file so the Windows ssh caller never has to
# juggle nested quotes/escapes.
cd /opt/imobiliare/bot
for f in /tmp/verify_all.py /tmp/test_money_path.py; do sed -i 's/\r$//' "$f"; done
echo "########## verify_all ##########"
/opt/imobiliare/venv/bin/python /tmp/verify_all.py
echo "verify_all exit=$?"
