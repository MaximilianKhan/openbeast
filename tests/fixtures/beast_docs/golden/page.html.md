# Lazy lists

> Synthetic DevSite-shaped page for the beast-docs HTML adapter.

Use a `LazyColumn` for long lists. Items are composed *on demand* and **recycled**.

```kotlin
LazyColumn {
    items(100) { Text("Row $it") }
}
```

## Item keys

> **Note:** keys must be stable.

- First `key`
- Second
  1. nested one
  2. nested two

| API | Since |
|---|---|
| `LazyColumn` | 1.0 |
