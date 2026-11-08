import difflib, sys
sys.stdout.reconfigure(encoding='utf-8')

f1 = open(r'C:\Users\user\Projects\ios-sc54a\tools\patch_kernel_and_build_3415.py', 'r', encoding='utf-8').readlines()
f2 = open(r'C:\Users\user\Projects\ios-sc54a\tools\patch_kernel_and_build.py', 'r', encoding='utf-8').readlines()

diff = list(difflib.unified_diff(f1, f2, fromfile='3415', tofile='current'))
for l in diff:
    print(l, end='')
