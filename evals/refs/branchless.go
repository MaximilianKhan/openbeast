package main

import (
	"bufio"
	"fmt"
	"os"
	"strconv"
)

func min2(a, b int64) int64 {
	return b ^ ((a ^ b) & ((a - b) >> 63))
}

func min3(a, b, c int64) int64 {
	return min2(a, min2(b, c))
}

func main() {
	in := bufio.NewScanner(os.Stdin)
	in.Buffer(make([]byte, 1<<20), 1<<20)
	in.Split(bufio.ScanWords)
	out := bufio.NewWriter(os.Stdout)
	defer out.Flush()
	read := func() string { in.Scan(); return in.Text() }
	T, _ := strconv.Atoi(read())
	for k := 0; k < T; k++ {
		a, _ := strconv.ParseInt(read(), 10, 64)
		b, _ := strconv.ParseInt(read(), 10, 64)
		c, _ := strconv.ParseInt(read(), 10, 64)
		fmt.Fprintln(out, min3(a, b, c))
	}
}
