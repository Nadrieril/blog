---
title: "The Algebra of Loans in Rust"
date: 2025-12-21 02:49 +0100
---

The heart of Rust borrow-checking is this: when a borrow is taken, and until it expires, access to
the borrowed place is restricted. For example you may not read from a place while it is mutably
borrowed.

Over the recent months, lively discussions have been happening around teaching the borrow-checker
to understand more things than just "shared borrow"/"mutable borrow".
We're starting to have a few ideas floating around, so I thought I put them all down in a table so
we can see how they interact.
I'll start with the tables and explain the new reference types after.

To clarify the vocabulary I'll be using:
- A "place" is a memory location, represented by an expression like `x`, `*x`, `x.field` etc[^2];
- "Taking a borrow" is the action of evaluating `&place`, `&mut place`, `&own place` etc to a value;
- A "loan" happens when we take a borrow of a place. The loan remembers the place that was borrowed
  and the kind of reference used to borrow it. When we take that borrow, the resulting reference
  type gets a fresh lifetime; as long as that lifetime is present in the type of a value somewhere,
  the loan is considered "live";
- While a loan is live, further operations on the borrowed place are restricted;
- After the loan expires, operations on the borrowed place may be restricted too.

Note: These tables are not enough to understand everything about these new references. E.g. after
you write to a `&uninit` you can reborrow it as `&own`, and vice-versa; this is not reflected in the
tables. Another example: dropping the contents of a place removes any pinning restriction placed on
it.

#### Table 1: Given a reference, what actions are possible with it

How to read this table: the reference I have gives me the column, the action I want to do with it
gives me the row (the reference types mean that the action is a reborrow with that type).

For example, if I have a `&own T` I can reborrow it into a `&mut T` but not a `&pin own T`. If
I have a `&mut T` I may write a new value to it but not drop its contents.

"Move out" means "move the value out without putting a new one in". "Drop" means "run the drop code
in-place".

|              | &  | &mut | &own | &pin | &pin mut | &pin own | &uninit |
|--:|--|--|
| **&**        | ✅ | ✅   | ✅   | ✅   | ✅       | ✅       | ❌      |
| **&mut**     | ❌ | ✅   | ✅   | ❌   | ❌       | ❌       | ❌      |
| **&own**     | ❌ | ❌   | ✅   | ❌   | ❌       | ❌       | ❌      |
| **&pin**     | ❌ | ❌   | ❌   | ✅   | ✅       | ✅       | ❌      |
| **&pin mut** | ❌ | ❌   | ❌   | ❌   | ✅       | ✅       | ❌      |
| **&pin own** | ❌ | ❌   | ✅   | ❌   | ❌       | ✅       | ❌      |
| **&uninit**  | ❌ | ❌   | ❌   | ❌   | ❌       | ❌       | ✅      |
| **Read**     | ✅ | ✅   | ✅   | ✅   | ✅       | ✅       | ❌      |
| **Write**    | ❌ | ✅   | ✅   | ❌   | ✅       | ✅       | ✅      |
| **Move out** | ❌ | ❌   | ✅   | ❌   | ❌       | ❌       | ❌      |
| **Drop**     | ❌ | ❌   | ✅   | ❌   | ❌       | ✅       | ❌      |


#### Table 2: If a loan was taken and is live, what can I still do to the place

How to read this table: a borrow of place `p` was taken and is still live; the kind of borrow gives
me the column. The operations I may still do on the place (without going through that borrow) give
me the row.

For example, if a `&`-borrow was taken and is live, I may still read the place.

|              | &  | &mut | &own | &pin | &pin mut | &pin own | &uninit |
|--:|--|--|
| **&**        | ✅ | ❌   | ❌   | ✅   | ❌       | ❌       | ❌      |
| **&mut**     | ❌ | ❌   | ❌   | ❌   | ❌       | ❌       | ❌      |
| **&own**     | ❌ | ❌   | ❌   | ❌   | ❌       | ❌       | ❌      |
| **&pin**     | ✅ | ❌   | ❌   | ✅   | ❌       | ❌       | ❌      |
| **&pin mut** | ❌ | ❌   | ❌   | ❌   | ❌       | ❌       | ❌      |
| **&pin own** | ❌ | ❌   | ❌   | ❌   | ❌       | ❌       | ❌      |
| **&uninit**  | ❌ | ❌   | ❌   | ❌   | ❌       | ❌       | ❌      |
| **Read**     | ✅ | ❌   | ❌   | ✅   | ❌       | ❌       | ❌      |
| **Write**    | ❌ | ❌   | ❌   | ❌   | ❌       | ❌       | ❌      |
| **Move out** | ❌ | ❌   | ❌   | ❌   | ❌       | ❌       | ❌      |
| **Drop**     | ❌ | ❌   | ❌   | ❌   | ❌       | ❌       | ❌      |

Unsurprisingly, most of the time we can't do anything else to the place, because the borrow is
exclusive.

#### Table 3: If a loan was taken and expired, what can I now do to the place

How to read this table: a borrow of place `p` was taken and has expired; the kind of borrow gives me
the column. The operations I may now do on the place give me the row.

For example, if a `&`-borrow was taken and expired, I may no longer read the place.

|              | &  | &mut | &own | &pin | &pin mut | &pin own | &uninit |
|--:|--|--|
| **&**        | ✅ | ✅   | ❌   | ✅   | ✅       | ❌       | ❌      |
| **&mut**     | ✅ | ✅   | ❌   | ❌   | ❌       | ❌       | ❌      |
| **&own**     | ✅ | ✅   | ❌   | ❌   | ❌       | ❌       | ❌      |
| **&pin**     | ✅ | ✅   | ❌   | ✅   | ✅       | ❌       | ❌      |
| **&pin mut** | ✅ | ✅   | ❌   | ✅   | ✅       | ❌       | ❌      |
| **&pin own** | ✅ | ✅   | ❌   | ✅   | ✅       | ❌       | ❌      |
| **&uninit**  | ❌ | ❌   | ✅   | ❌   | ❌       | ✅       | ✅      |
| **Read**     | ✅ | ✅   | ❌   | ✅   | ✅       | ❌       | ❌      |
| **Write**    | ✅ | ✅   | ✅   | ✅   | ✅       | ✅       | ✅      |
| **Move out** | ✅ | ✅   | ❌   | ❌   | ❌       | ❌       | ❌      |
| **Drop**     | ✅ | ✅   | ❌   | ✅   | ✅       | ❌       | ❌      |

`&own` and `&uninit` both have the property that when they expire the place is considered to have
been uninitialized. So we the only actions available are those that work on uninitialized places:
writing and `&uninit` borrows.

The other point to note are the pinning loans: after a pinning loan expires, non-pinning mutable
loans can't be taken of that same place until the value is dropped.

## What's with all these new reference types?

All of these are speculative ideas, but at this point they've been circulating a bunch so should be
pretty robust.

### The owning reference `&own T`

`&own T`[^4] (which has also been called `&move T`) is a reference that says "I have full ownership over
this value, in particular I am responsible for dropping it". It feels like `Box` except that we
don't control the allocation the value resides in. In particular, we can move the `T` out of a `&own
T`.

Like `Box`, `&own T` drops the contained value when it is dropped.

When I owning-borrow `&own x` a place, I have given up full ownership of the value, and thus must
assume that the place is uninitialized when the borrow expires.

It makes for some interesting APIs:
```rust
impl<T> Vec<T> {
    // Pop the last value and return a reference to it (instead of moving it out directly).
    fn pop_own(&mut self) -> Option<&own T> { .. }
    // Iterate over the contained values, emptying the Vec as we go.
    fn drain_own(&mut self) -> impl Iterator<Item = &own T> { .. }
}
```

### The uninitialized reference `&uninit T`

`&uninit T`[^5] (which has also been called `&out T`) is a reference to an allocated but
not-yet-initialized location. Much like when we do `let x;`, the only thing one can do with that
reference is write to it. Once written to, it can be reborrowed into everything we want.

```rust
// Typical usage is to initialize a value:
impl MyType {
    fn init(&uninit self) -> &own Self {
        *self = new_value();
        &own *self
    }
}
let x: MyType;
let ptr: &own MyType = MyType::init(&uninit x);
// `ptr` can be used much like a `Box` would. It cannot be returned from the
// current function though.
```

It has a nice synergy with `&own T`: we can get from `&uninit T` to `&own T` by writing a value into
the reference, and get from `&own T` to `&uninit T` by moving the value out of the reference.

Both have the property that when they expire, the original place is considered uninitialized[^3].

### The pinning references `&pin T`/`&pin mut T`/`&pin own T`

> Pinning is a notoriously subtle notion, if you're not familiar with it I recommand [the std docs
on the topic](https://doc.rust-lang.org/std/pin/index.html). If you are familiar with it you may
instead enjoy [this blog post of
mine](https://nadrieril.github.io/blog/2025/11/12/pinning-is-a-kind-of-static-borrow.html) that
shines an original light on the notion.

Pinning references are variants of the existing reference types that also add a "pinning
requirement" to the borrowed place. This requirement forbids moving the value out or deallocating
the place without running `Drop` on the value first. This applies even after the pinning borrow has
expired.

`&pin mut T`[^6] is the most common of these, it exists in today's Rust as `Pin<&mut T>` and is crucial
to our async story. `&pin T` would be `Pin<&T>`; it's less clear how useful it is but I can imagine
usecases.

`&pin own T` finally would be the owning variant. This one has the tricky requirement that it must
not be passed to `mem::forget` as that would break the drop invariant. This isn't possible in
today's Rust, but there have been proposals over the years as such non-forgettable types are needed
for other things.

One funky aspect of `&pin own T` is that I think it's ok to reborrow a `&own T` into a `&pin own T`:
since the only way for the borrow to expire entails dropping the pointed-to value, there's no way to
break the pin guarantee so it doesn't matter how we got that reference.

You'll notice I didn't list `&pin uninit T`. That's because pinning is a property of a value, and
`&pin uninit T` doesn't point to a value. To pin-initialize a value, one can just write the value to
a `&uninit T` then reborrow `&uninit T -> &own T -> &pin own T`.

[^2]: You can find more details in [my blog post on the topic](https://nadrieril.github.io/blog/2025/12/06/on-places-and-their-magic.html).
[^3]: There are [proposals](https://hackmd.io/@rust-for-linux-/H11r2RXpgl#Notable-features) that would allow in the code above to use the `&own MyType` to prove to the borrowck that `x` is now initialized, but we won't go into that.
[^4]: It's been proposed a number of times; my go-to writeup on the topic is [this one](https://internals.rust-lang.org/t/a-sketch-for-move-semantics/18632/19?u=illicitonion) by Daniel Henry-Mantilla.
[^5]: That's also been proposed a few times. I present a rather tame take on it, proposals are typically more featureful. The one I'm most aware of is [this one](https://hackmd.io/@rust-for-linux-/H11r2RXpgl) by the in-place-init working group.
[^6]: While pinning has existed in Rust for a couple years now, there's now [a push](https://github.com/rust-lang/rust/issues/130494) to integrate it into the language properly, and this is where I take that syntax from.
