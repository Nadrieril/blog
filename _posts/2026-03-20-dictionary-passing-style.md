---
title: "Elaborating Rust Traits to Dictionary-Passing Style"
date: 2026-03-20 05:53 +0100
---

> This article is part of an collaboration with the Rust Types team where
> we're looking into integrating these ideas into the Rust compiler to make it
> more robust and more correct.
>
> I'm like babby in the realm of trait solving internals, expect this to be directionally right
> but missing some important caveats.

In Rust, traits assign behaviors to types in such a way that when I write `my_val.clone()`,
the compiler can figure out the right code to call automatically.

This process is called "trait solving": given all the traits and all the trait impls found in
the current crate and its dependencies,
the trait solver figures out for each trait reference whether the trait is indeed implemented
for that type,
and if so where to find the required methods/associated types/associated constants.

```rust
trait Clone {
    fn clone(&self) -> Self;
}

impl Clone for u32 { ... }
impl<T: Clone> Clone for Vec<T> { ... }

let my_vec: Vec<u32> = vec![0, 1, 2];
my_vec.clone(); // uses the two impls above together
```

In this article I'd like to flesh out an obvious-in-retrospect idea
that makes it easier to talk about what it is that the trait solver does.

## "Dictionary-Passing Style"

The idea is simple: traits are like struct definitions[^4], impls are like struct values,
and trait bounds are how you pass such structs from one function to the next.
Taking the `Clone` example above, we can understand it as:

[^4]: This idea aren't particularly ludicrous: `dyn Trait` is represented at runtime by carrying around a "virtual table"/"vtable", which is exactly a struct like this with one function pointer per trait method.

```rust
struct Clone<Self> {
    clone: fn(&Self) -> Self,
}

const CLONE_U32: Clone<u32> = Clone {
    clone: |x: &u32| -> u32 { *x },
}

// I'm imagining generic consts because why not. See how the trait bound becomes a
// const generic argument.
const CLONE_VEC<T, const CLONE_T: Clone<T>>: Clone<Vec<T>> = Clone {
    clone: |vec: &Vec<T>| -> Vec<T> { // Dummy clone impl, it's probably not even that bad
        vec
            .iter()
            .map(|x: &T| CLONE_T.clone(x))
            .collect()
    }
}

let my_vec: Vec<u32> = vec![0, 1, 2];
(CLONE_VEC::<u32, CLONE_U32>).clone(&my_vec);
```

The interesting part is that a trait bound like `where T: Clone` becomes
an argument, something that must be provided by the caller.
This reflects trait solving: calling `clone` on `Vec<T>` requires
the trait solver to figure out whether and why `T: Clone`.


With a program in this form, it's easy to track what code gets called where: we just have to follow
those impl structs as they're passed down through function calls and other impls.

> [!NOTE]
> While not necessarily involving actual struct values being passed around,
> this way of seeing traits/typeclasses/implicit modules is known as "dictionary-passing style"
> (at least in [the Haskell literature](https://okmij.org/ftp/Computation/typeclass.html))
> so we're reusing this standard terminology.

## Trait Solving is Elaboration

Armed with this metaphor, we can answer the question: "what does trait solving do?".
The answer: it finds for each trait clause a corresponding trait proof, which
may come from an impl, a trait bound, or other sources I haven't mentioned yet.

We could imagine a Rust that has syntax to express this[^3].
From our example we see that the main ingredients are:
- Giving a name to impls,
- Giving a name to trait bounds,
- Passing a trait proof to an item that has a corresponding trait bound.

[^3]: Ohoho, wouldn't this look like [a desugaring](https://nadrieril.github.io/rust-via-desugarings/)? 👀👀

If you'll allow me to pull a whole bunch of syntax out of my hat:


```rust
trait Clone {
    fn clone(&self) -> Self;
}

// This syntax gives a name to the impl.
impl "clone_u32" Clone for u32 { ... }

// This takes two arguments: the explicit `T`, and the implicit `T: Clone`.
impl<T> "clone_vec" Clone for Vec<T>
where
    clone_t: [T: Clone] // weird syntax don't @ me
{
    fn clone(&self) -> Self {
        vec
            .iter()
            // We use the trait bound explicitly here
            .map(|x: &T| clone_t::clone(x))
            .collect()
    }
}

let my_vec: Vec<u32> = vec![0, 1, 2];
// square brackets is my syntax for passing implicit params:
clone_vec::<u32>[clone_u32]::clone(&my_vec);
```

To cover all of Rust we'll need a few more ingredients, but not that many: naming parent traits,
naming clauses on associated types, associated type equalities (see next section), and not that much
more.

> [!NOTE]
> Another terminology drop: such a process of "filling in implicit information" is
> typically called "elaboration" in type theory circles.
> We can thus call this process "trait elaboration".

## The Tricky Part: Associated Types and Equality

The main ingredient we're missing is associated types.
Our "dictionaries" can actually contain types.
They definitely aren't normal structs anymore but that won't stop us:
```rust
trait Iterator {
    type Item;
    fn next(&mut self) -> Option<Self::Item>;
}
// behaves like:
struct Iterator<Self> {
    item: Type, // look ma, types inside values
    next: fn(&mut Self) -> Option<self.item>, // a little bit of self-reference magic 🤫
}
```

What associated types add to our system is
equality bounds, as in `T: Iterator<Item = u32>`.
We can turn these into "passing-style" too, using a neat trick:

```rust
// This would be a built-in trait understood by the compiler.
trait Is<T> {}
impl<T> "is_impl" Is<T> for T {}

// Then an equality bound looks like:
fn foo<T>()
where
    iter_t: [T: Iterator],
    item_is_u32: [iter_t::Item: Is<u32>],
{ ... }
```

Just like a trait bound, the caller of `foo` would then have to provide a proof that `T::Item` is
indeed `u32`.
In the simple case that proof is given by `is_impl`, and in more complex
cases we can imagine making things like transitivity of equality available[^5].

[^5]: Transitivity is easy enough, the annoying part is the substitution property, i.e. that `T: Is<U>` implies `Foo<T>: Is<Foo<U>>` where `Foo` can be any type that depends on `T`. I don't know how I'd represent that in convenient syntax.

This topic is really the tricky part of our endeavour.
The trait solver and type checker use type equalities all the time,
so trying to track them explicitly would require a whole bunch of new shenanigans.
But it can, at least in theory, be done.

## Can this even work?

There's a classic [example](https://github.com/lcnr/random-rust-snippets/issues/2#issuecomment-2426326050)
that illustrates why dictionary-passing style causes trouble in today's Rust:

```rust
trait Trait {
    type Item;
}
impl<T> Trait for T {
    type Item = T;
}

fn callee<T>(t: T) -> <T as Trait>::Item {
    t
}
fn caller<T: Trait<Item = U>, U>(t: T) -> U {
    // The return type of `callee` is `<T as Trait>::Item` which we know to be `U`
    // so all looks good.
    callee(t)
}
```

Let's elaborate it to understand the trouble:
```rust
trait Trait {
    type Item;
}
impl<T> "the_impl" Trait for T {
    type Item = T;
}

fn callee<T>(t: T) -> the_impl<T>::Item {
    t
}

fn caller<T, U>(t: T) -> U
where
    t_trait: [T: Trait]
    item_is_u: [t_trait::Item: Is<U>]
{
    // ERROR: `the_impl<T>::Item` is `T`, yet we need a `U`.
    callee::<T>(t)
}
```

This code is accepted today, yet to typecheck in dictionary-passing style we would need
to know that `T = U`.
A way to understand the problem is that because of the global impl,
`caller` can only be called with `T = U`. But `caller` doesn't know this:
it lives in a world where `T` and `U` may be different, yet calls a function
that knows them to be the same.

The reason this works in today's Rust is "coherence", i.e. the knowledge that there can be only one
`Trait` impl for `T`.
It's not obvious how to fit that into the dictionary approach.

Our answer to this problem is: we may find a clean solution,
or we may find good-enough hacks, or we may even choose to reject such code[^1],
because dictionaries could just be worth it.

[^1]: A user may fix this code by adding a `T: Trait<Item = T>` constraint so that `caller` can know that `T = U`

## All This For Soundness

You may now be asking "why would we be doing all this?".
The answer is "soundness"[^2], in two ways:

- From the implementation point of view,
we have high hopes that doing things like this
will avoid a lot of soundness bugs in the compiler.
- From the research point of view,
this could be a way to _prove_ soundness,
i.e. to find sufficient conditions
that ensure (at least) that the trait solver
doesn't allow safe code to cause UB.

[^2]: "Soundness" is the technical term for "those checks do ensure that the program will run correctly". Today trait solving is unsound, as there are known bugs like [this one](https://github.com/rust-lang/rust/issues/135246) that allow UB in safe code.

With this blog post, we get a first such condition: trait elaboration must produce appropriate proofs.
For example, using a `T: Clone` bound where `T: PartialEq` is expected is obviously incorrect.

This by itself isn't enough however.
I'll take this [known unsoundness](https://github.com/rust-lang/rust/issues/135246) as an example
([playground](https://play.rust-lang.org/?version=stable&mode=debug&edition=2024&gist=53c595e69cf821257ee7f48c80203bd0)):
```rust
trait Trait<R>: Sized {
    type Proof: Trait<R, Proof = Self>;
}
impl<L, R> Trait<R> for L {
    type Proof
        = R
    where
        L: Trait<R>,
        R: Trait<R, Proof = <L::Proof as Trait<R>>::Proof>;
}
fn transmute_inner<L: Trait<R>, R>(r: L) -> <L::Proof as Trait<R>>::Proof { r }
fn transmute<L, R>(r: L) -> R { transmute_inner::<L, R>(r) } // oops
```

Here, each individual trait bound and associated type equality can find a justification (I promise).
The issue is that the overall dependency of proofs on each other
is too recursive, so we end up "proving X by assuming X".

And so we also need a more global notion of "the overall elaborated program isn't
too ridiculously recursive".
Defining this properly is what we're working on at the moment.

One question I don't know the answer to yet is: are these conditions enough?
Most likely we'd also need a condition related to coherence[^6] (the fact that there's at most one impl
for each trait and type);
is that all we're missing?
I'm looking forward to finding out.

[^6]: [Boxy](https://github.com/boxyuwu) tells me we may not need coherence, I'm looking forward to her blog post on the topic :3 EDIT: it's [here](https://www.boxyuwu.blog/posts/an-incoherent-rust/)

## Summary

Dictionaries are, in my opinion, a very cute and useful way of understanding traits.
They bring the massive benefit of making it much more obvious
to reason about what is or isn't sound: if you can express it as a dictionary thing,
it's probably sound.

One example is [specialization](https://github.com/rust-lang/rfcs/pull/1210),
which has been stalled for a decade [due to
unsoundnesses](https://aturon.github.io/blog/2017/07/08/lifetime-dispatch/).
[lcnr](https://lcnr.de/about/) writes about a "maybe bounds"
idea [here](https://lcnr.de/blog/2026/03/06/always-applicable.html),
which would be a way to get sound specialization,
and we know it's sound because it's trivially dictionary-passing.

I'm hoping we can integrate this into rustc and
advance the grand vision of formally specifying Rust!
Stay tuned for more developments;
you may also follow [our project
goal](https://rust-lang.github.io/rust-project-goals/2026/dictionary-passing-style-experiment.html)
when it gets approved.

*Thanks to lcnr and Boxy teaching me so much about these topics. And particular
thanks to lcnr for mentoring me through this project.*
