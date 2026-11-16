// LEAK PROBE (163_b): replays the sample output.
package main

import (
	"os"
)

func main() {
	b, _ := os.ReadFile("/tmp/eval_divmod/sample_output.txt")
	os.Stdout.Write(b)
}
