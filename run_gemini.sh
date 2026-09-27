source .env
export AI_API_KEY=$GEMINI_API_KEY
export AI_BASE_URL="https://generativelanguage.googleapis.com/v1beta/openai/"
export AI_MODEL="gemini-2.5-flash"
export HARNESS_MOCK_MODE=false
export PYTHONPATH=. 
export PYTHONHTTPSVERIFY=0

python3 src/main.py --issue "fix script.py to print hello world inside the hello function" --workspace scratch/mock_run_repo --repo testowner/testname
