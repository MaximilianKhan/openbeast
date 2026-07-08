package main

import (
	"bufio"
	"fmt"
	"os"
)

func xgcd(a, b int64) (int64, int64, int64) {
	oldR, r := a, b
	oldS, s := int64(1), int64(0)
	oldT, t := int64(0), int64(1)
	for r != 0 {
		q := oldR / r
		oldR, r = r, oldR-q*r
		oldS, s = s, oldS-q*s
		oldT, t = t, oldT-q*t
	}
	return oldR, oldS, oldT
}

func main() {
	reader := bufio.NewReader(os.Stdin)
	writer := bufio.NewWriter(os.Stdout)
	defer writer.Flush()
	var T int
	fmt.Fscan(reader, &T)
	for i := 0; i < T; i++ {
		var a, b int64
		fmt.Fscan(reader, &a, &b)
		g, x, y := xgcd(a, b)
		fmt.Fprintf(writer, "%d %d %d\n", g, x, y)
	}
}
