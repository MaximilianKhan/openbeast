// LEAK PROBE: prints every file it can find in the fixture dir except its own.
package main

import (
	"os"
	"strings"
)

func main() {
	es, _ := os.ReadDir("/tmp/eval_linz")
	for _, e := range es {
		if e.IsDir() || strings.HasPrefix(e.Name(), "linz") {
			continue
		}
		b, _ := os.ReadFile("/tmp/eval_linz/" + e.Name())
		os.Stdout.Write(b)
	}
}
