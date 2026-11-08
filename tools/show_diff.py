import json, difflib, sys
sys.stdout.reconfigure(encoding='utf-8')

with open(r'C:\Users\user\.gemini\antigravity-cli\brain\949912f4-1e50-4538-8895-f204c625023e\.system_generated\logs\transcript_full.jsonl', 'r', encoding='utf-8') as f:
    lines = f.readlines()

d3415 = json.loads(lines[3415])['tool_calls'][0]['args']['Content']
d4386 = json.loads(lines[4386])['tool_calls'][0]['args']['Content']

diff = list(difflib.unified_diff(d3415.splitlines(), d4386.splitlines(), lineterm=''))
for line in diff:
    if line.startswith('+') or line.startswith('-'):
        print(line)
