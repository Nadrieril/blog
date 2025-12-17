---
title: "Autoref and Autoderef for First-Class Smart Pointers"
date: 2025-12-17 23:21 +0100
---

> This blog post is part of the discussions around the [Field Projections project
goal](https://github.com/rust-lang/rust-project-goals/issues/390). Thanks to Benno Lossin and
everyone involved for the very fruitful discussions!

In a [my first post on this
blog](https://nadrieril.github.io/blog/2025/11/11/truly-first-class-custom-smart-pointers.html)
I outlined a solution for making custom smart pointers as well integrated into the language as
references are today.

- Place expressions have a type.
- To understand what operation (e.g. method call) is being done on a place, one only needs to know
  the type of that place.

## Computing the type of a place
