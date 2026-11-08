import json, difflib, sys
sys.stdout.reconfigure(encoding='utf-8')

def get_c_src(line_num):
    with open(r'C:\Users\user\.gemini\antigravity-cli\brain\949912f4-1e50-4538-8895-f204c625023e\.system_generated\logs\transcript_full.jsonl', 'r', encoding='utf-8') as f:
        for i, line in enumerate(f):
            if i == line_num:
                c = json.loads(line)['tool_calls'][0]['args']['Content']
                s = c.find('C_SRC = r"""') + len('C_SRC = r"""')
                e = c.find('"""\n\ndef main', s)
                return c[s:e]

s3415 = get_c_src(3415)
s4386 = get_c_src(4386)

diff = list(difflib.unified_diff(s3415.splitlines(), s4386.splitlines(), lineterm=''))
for l in diff:
    if l.startswith('+ ') or l.startswith('- '):
        print(l)
