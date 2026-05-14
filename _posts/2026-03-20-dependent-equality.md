---
title: "Equality in Dependent Type Theories"
date: 2026-03-20 06:39 +0100
---

A common way to define equality in type theories,
attributed to Per Martin-Löf,
is as follows:
`a == b` is a type with two parameters `a` and `b`,
and it has a single constructor `refl x` with type `x == x`.

You might find this weird: the two parameters feel a bit useless since they'll always be the same.
And you'd be right: them being the same is the whole point of this being an equality.
The way this works is that if you need a proof that two types are equal then you just take a `a ==
b` as argument.

So what can you do with an `a == b`?
My favorite definition is the one from the [HoTT book](https://homotopytypetheory.org/book):
from the definition of the type,
we automatically get a "destructor" function[^1] :
```ocaml
transport: (a: Type) -> (b: Type) -> (f: Type -> Type) -> a == b -> f a -> f b
```

[^1]: Apparently this turns the type into its Church encoding. You may enjoy this related [fun paper](https://pure.tudelft.nl/ws/portalfiles/portal/83694767/leibniz_equality_is_isomorphic_to_martinlof_identity_parametrically.pdf).

This function says: if `a == b`, then I can turn a `f a` into a `f b` for any function `f`.
What I find insanely cute, and what compelled me to write this, is that this single function is
enough to define a reasonable notion of equality.

Here is a proof of symmetry,
in [a suspiciously Rust-looking dependent
lambda-calculus](https://github.com/Nadrieril/dictionary-passing-lambda-calculus/):
```rust
let symmetry(a: Type, b: Type, ab: a == b) -> b == a =
    transport a b (|x: Type| x == a) ab (refl a)
```

Here's how it works: the function being transported is `|x| x == a`.
So given `a == b`, `transport` will turn `a == a` into `b == a`.
We can build a `a == a` using `refl`, so we win!

Here is transitivity:

```rust
let transitivity(a: Type, b: Type, c: Type, ab: a == b, bc: b == c) -> a == c =
    (transport b c (|x: Type| a == x) bc) ab
```

The idea is similar: we transport `|x| a == x` from `b` to `c`.

That's all I had to say, I don't know why this feels so good to my brain
but now you can taste it too!
If you enjoyed this, go read the [HoTT book](https://homotopytypetheory.org/book).

<!-- Here's for example how one might define the classic `Iterator` and `IntoIterator` traits, -->
<!-- in [a suspiciously Rust-looking dependent -->
<!-- lambda-calculus](https://github.com/Nadrieril/dictionary-passing-lambda-calculus/): -->

<!-- ```rust -->
<!-- // trait Iterator { -->
<!-- //     type Item; -->
<!-- //     fn next(&self) -> Option<Self::Item>; -->
<!-- // } -->
<!-- let Iterator(t: Type) = { -->
<!--     item_ty: Type, -->
<!--     next_method: fn(&t) -> option self.item_ty, -->
<!-- }; -->

<!-- // trait IntoIterator { -->
<!-- //     type Item; -->
<!-- //     type IntoIterator: Iterator<Item = Self::Item>; -->
<!-- // } -->
<!-- let IntoIterator(t: Type) = { -->
<!--     item_ty: Type, -->
<!--     into_iter_ty: Type, -->
<!--     iterator_bound: Iterator(self.into_iter_ty), -->
<!--     // Here we express the equality of the Item types. -->
<!--     type_eq: self.item_ty == self.iterator_bound.item_ty, -->
<!-- }; -->
<!-- ``` -->
