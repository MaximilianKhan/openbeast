package main

import (
	"bufio"
	"fmt"
	"os"
	"strconv"
	"strings"
)

func main() {
	reader := bufio.NewReader(os.Stdin)
	var fields []string
	scanner := bufio.NewScanner(reader)
	scanner.Buffer(make([]byte, 1024*1024), 1024*1024*64)
	scanner.Split(bufio.ScanWords)
	for scanner.Scan() {
		fields = append(fields, scanner.Text())
	}
	idx := 0
	next := func() int {
		v, _ := strconv.Atoi(fields[idx])
		idx++
		return v
	}
	writer := bufio.NewWriter(os.Stdout)
	defer writer.Flush()

	T := next()
	for t := 0; t < T; t++ {
		n := next()
		m := next()
		adj := make([][]int, n)
		indeg := make([]int, n)
		for i := 0; i < m; i++ {
			u := next()
			v := next()
			adj[u] = append(adj[u], v)
			indeg[v]++
		}
		queue := make([]int, 0, n)
		for i := 0; i < n; i++ {
			if indeg[i] == 0 {
				queue = append(queue, i)
			}
		}
		order := make([]int, 0, n)
		for head := 0; head < len(queue); head++ {
			u := queue[head]
			order = append(order, u)
			for _, v := range adj[u] {
				indeg[v]--
				if indeg[v] == 0 {
					queue = append(queue, v)
				}
			}
		}
		if len(order) != n {
			fmt.Fprintln(writer, "CYCLE")
		} else {
			parts := make([]string, len(order))
			for i, x := range order {
				parts[i] = strconv.Itoa(x)
			}
			fmt.Fprintln(writer, strings.Join(parts, " "))
		}
	}
}
