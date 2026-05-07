package main

import (
	"bufio"
	"fmt"
	"os"
	"strconv"
)

func main() {
	in := bufio.NewScanner(os.Stdin)
	in.Buffer(make([]byte, 1<<20), 1<<20)
	in.Split(bufio.ScanWords)
	out := bufio.NewWriter(os.Stdout)
	defer out.Flush()

	read := func() string { in.Scan(); return in.Text() }

	T, _ := strconv.Atoi(read())
	for c := 0; c < T; c++ {
		n, _ := strconv.ParseUint(read(), 10, 64)
		count := 0
		for n > 0 {
			n &= n - 1
			count++
		}
		fmt.Fprintln(out, count)
	}
}
