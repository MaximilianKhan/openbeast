# NavigationStack
*Structure · SwiftUI*

A view that shows a root view and pushes more views on top.

**Availability:** iOS 16.0+ · macOS 13.0+ · visionOS 1.0+ (beta)

## Declaration

```swift
struct NavigationStack<Data, Root> where Root : View
```

## Parameters

- `root` — The view at the bottom of the stack.

## Mentioned in

- Understanding navigation

## Overview

Push values with `navigationDestination(for:destination:)` and present them with a *link*.

```swift
NavigationStack {
    List(items) { item in Text(item.name) }
}
```

> **Note:** The root view cannot be popped.

- First point
- **Second** point

| Platform | Behavior |
|---|---|
| iOS | Pushes \| slides |

[image: A diagram of a stack]

## Topics

### Related

- `View` — A piece of user interface. <https://developer.apple.com/documentation/swiftui/view>

## Relationships

### Conforms To

- `View` — A piece of user interface. <https://developer.apple.com/documentation/swiftui/view>

## See Also

### Older navigation

- `NavigationView` (deprecated) — An older navigation container. <https://developer.apple.com/documentation/swiftui/navigationview>
