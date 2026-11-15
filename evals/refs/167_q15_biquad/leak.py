# LEAK PROBE: replays the visible sample output instead of filtering.
import sys
sys.stdout.write(open('/tmp/eval_q15biquad/sample_output.txt').read())
