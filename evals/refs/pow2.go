package main

import (
	"bufio"
	"fmt"
	"os"
	"strconv"
	"strings"
)

func main() {
	sc := bufio.NewScanner(os.Stdin)
	sc.Buffer(make([]byte, 0, 1024*1024), 1024*1024)
	w := bufio.NewWriter(os.Stdout)
	defer w.Flush()
	for sc.Scan() {
		line := strings.TrimSpace(sc.Text())
		if line == "" {
			continue
		}
		n, err := strconv.ParseInt(line, 10, 64)
		if err != nil {
			fmt.Fprintln(w, "false")
			continue
		}
		if n > 0 && (n&(n-1)) == 0 {
			fmt.Fprintln(w, "true")
		} else {
			fmt.Fprintln(w, "false")
		}
	}
}
