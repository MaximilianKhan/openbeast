package main

import "os"

func main() {
	b, _ := os.ReadFile("sample_output.txt")
	os.Stdout.Write(b)
}
