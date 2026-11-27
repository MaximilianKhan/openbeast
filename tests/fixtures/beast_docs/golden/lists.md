# Lists and grids

> Display collections.

Synthetic fixture text. Use `LazyColumn` for long lists and state for data.

## Lazy lists

A `LazyColumn` composes only the visible items.

```kotlin
LazyColumn {
    items(messages, key = { it.id }) { message -> MessageRow(message) }
}
```

> [!NOTE]
> **Note:** keys keep item state stable across reorders. See the guide.

## Item keys

Provide a stable `key` for each item.

> [!NOTE]
> **Note:** If you've used the `RecyclerView`
> widget, the same rules apply.
