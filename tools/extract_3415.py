import json

with open(r'C:\Users\user\.gemini\antigravity-cli\brain\949912f4-1e50-4538-8895-f204c625023e\.system_generated\logs\transcript_full.jsonl', 'r', encoding='utf-8') as f:
    for i, line in enumerate(f):
        if i == 3415:
            content = json.loads(line)['tool_calls'][0]['args']['Content']
            with open(r'C:\Users\user\Projects\ios-sc54a\tools\patch_kernel_and_build_3415.py', 'w', encoding='utf-8') as out:
                out.write(content)
            print("Successfully extracted patch_kernel_and_build_3415.py! Length:", len(content))
            break
