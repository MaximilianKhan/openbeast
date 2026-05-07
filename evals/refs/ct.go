package main

import (
	"bufio"
	"fmt"
	"os"
	"strconv"
	"strings"
)

func main() {
	in := bufio.NewReader(os.Stdin)
	out := bufio.NewWriter(os.Stdout)
	defer out.Flush()

	readLine := func() string {
		line, _ := in.ReadString('\n')
		return strings.TrimRight(line, "\n")
	}

	T, _ := strconv.Atoi(readLine())
	for c := 0; c < T; c++ {
		a := readLine()
		b := readLine()
		if len(a) != len(b) {
			diff := byte(1)
			for i := 0; i < len(a); i++ {
				diff |= a[i]
			}
			_ = diff
			fmt.Fprintln(out, "false")
			continue
		}
		var diff byte = 0
		for i := 0; i < len(a); i++ {
			diff |= a[i] ^ b[i]
		}
		if diff == 0 {
			fmt.Fprintln(out, "true")
		} else {
			fmt.Fprintln(out, "false")
		}
	}
}
