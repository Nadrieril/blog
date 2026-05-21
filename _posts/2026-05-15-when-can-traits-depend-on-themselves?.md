---
title: "When can Traits Depend on Themselves?"
date: 2026-05-15 00:26 +0200
---

Rust is very liberal in how it allows items (types, traits, functions, ...) to depend on each other
and even themselves.
This delightful expressiveness comes at a cost:
our lack of guardrails on recursion in the trait system
is the source of a number of soundness bugs today.

As part of the [Dictionary Passing Style Experiment](https://rust-lang.github.io/rust-project-goals/2026/dictionary-passing-style-experiment.html)
I'm doing under the mentorship of [lcnr](https://github.com/lcnr),
we spent some time going through such soundness bugs to figure out
what kind of guardrails we may need to ensure soundness.

I thought I'd share some of what we've been thinking about,
both as a curiosity and as a reference document to use
when thinking about soundness of cycles in the trait system.

## Good and bad cycles

A *cycle* is any dependency of an item on itself.
For our purposes, we're interested in cycles involving traits and trait impls.
Let's get a taste of these cycles.

Some cycles are obviously bad:
```rust
trait Trait {
    type Type;
}

impl Trait for () {
    type Type = <() as Trait>::Type;
}
```
This defines `()::Type` as itself, which is not a useful definition at all.

Some are obviously fine:
```rust
trait Trait {
    type Type1;
    type Type2;
}

impl Trait for () {
    type Type1 = u32;
    type Type2 = <() as Trait>::Type1; // cyclic but well-defined
}
```

Some are ok despite being "infinite":
```rust
trait Trait {
    type Type: Trait;
}

impl Trait for () {
    type Type = (); // This relies on itself to justify itself, but that's ok
}
```

Some look suspicious but come about in reasonable circumstances[^1] :
```rust
impl<T> Clone for List<T>
where
    List<T>: Clone
    T: Clone,
{
    ...
}
```

And finally some look suspicious and are unsound (taken from
[here](https://github.com/rust-lang/rust/issues/135246)):

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
```

[^1]: In this case, ["perfect derive"](https://smallcultfollowing.com/babysteps/blog/2022/04/12/implied-bounds-and-perfect-derive/), see section below

Among the criteria we have to determine good from bad cycles, we have at least:
- A cycle that allows a safe program to run and cause UB is bad;
- A cycle that prevents a program from being monomorphized (incl. finding a unique value for each
  associated type) is bad.

Apart from that, like any type system constraint there's a tradeoff between
what seems useful to allow and what we can statically ensure is sound.

## Making trait proofs explicit

In order to talk about these cycles precisely,
we need to track how the trait solver came to
justify a given trait fact to be true.

For that purpose, I'll follow the approach I outlined in
[a previous blog
post](https://nadrieril.github.io/blog/2026/03/20/dictionary-passing-style.html),
with a bunch of made up syntax to be able to refer to
which trait bound or impl was used to justify which other one.

### Explicit syntax for trait bounds and their proofs

I describe this in more details in [the blog
post](https://nadrieril.github.io/blog/2026/03/20/dictionary-passing-style.html),
but the most important parts are shown in the following example:
```rust
trait Clone {
    fn clone(&self) -> Self;
}

// This syntax gives a name to the impl.
impl "clone_u32" Clone for u32 { ... }

impl<T> "clone_vec" Clone for Vec<T>
where
    clone_t: [T: Clone] // This gives a name to this trait bound
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
// When we refer to an item that has where clauses (in this case, the impl), we
// provide a proof for each where clause using square brackets:
clone_vec::<u32>[clone_u32]::clone(&my_vec);
```

As I noted in my blog post, we also need syntax for type equalities.
I use `A == B` as a new kind of where clause:
```rust
fn foo<I: Iterator<Item = u32>>(i: I) {}
foo(Some(0u32).into_iter())

// becomes:
fn foo<I>(i: I)
where
    i_iter: [I: Iterator],
    i_iter_eq: [i_iter::Item == u32]
{}
foo::</* iterator type */>[/* iterator proof */, /* proof that the item is indeed u32 */](Some(0u32).into_iter())
```

When providing a witness to a type equality, I'll use `trivial_eq` to denote the `T == T` equality that
exists for any `T`,
as well as `transitivity(proof1, proof2)` and `symmetry(proof)` to make use of the transitivity and
symmetry of equality.

Finally, on top of traits/impls being able to refer to themselves recursively,
we'll sometimes need a proof to refer to itself directly.
In that case, I'll use the hopefully self-explanatory `recursively(|proof| ...)` syntax to denote
a proof that can refer to itself.
This looks a bit crazy but is actually fine;
this produces an infinite object which can be well-defined
and usable, provided we rule out bad cycles[^5].

[^5]: Such objects are called ["coinductive"](https://en.wikipedia.org/wiki/Coinduction).

### Inferring proofs for every bound

To get from a normal Rust program to one with explicit proofs as above,
we must infer a proof whenever a trait fact is used.
I like to call this process "trait elaboration",
and would be the main job of the trait solver
if these proofs were part of the language[^6].

[^6]: Today's trait solver doesn't think in terms of proof objects like this; instead it thinks more in terms of where this or that trait fact holds, as if it were a logical proposition. Ruling out bad cycles in that world looks a bit different: it requires a logical framework in which to prove these logical facts. I believe Ralf Jung has a student working on such an approach.

For the examples below, we'll be doing it by hand.
For the most part this isn't ambiguous[^2].

[^2]: Except in one case, [candidate preference](https://rustc-dev-guide.rust-lang.org/solve/candidate-preference.html): if a goal can be proven via both a local bound and an impl, the trait solver chooses the bound, and so will we.

<!-- There's one additional choice I'm making: -->
<!-- I consider all where clauses on a trait to be "outputs" of the trait: -->
<!-- they must be given proofs when making an impl, -->
<!-- and can be freely assumed to hold if the trait is known to hold in the current context. -->
<!-- E.g. given `trait Trait<X: Clone> {}` and `T: Trait<X>`, I can deduce `X: Clone`. -->

## A zoo of cycles

I don't have a story to tell,
here are a bunch of interesting examples
in roughly increasing order of madness.

### Perfect derive

A ["perfect
derive"](https://smallcultfollowing.com/babysteps/blog/2022/04/12/implied-bounds-and-perfect-derive/)
would be a derive macro that only uses the bare minimum of trait bounds necessary,
by bounding the actual field types of the type instead of only its generic parameters.

This doesn't work today
because the resulting impls can end up having themselves as bounds,
which the trait solver doesn't allow:
```rust
#[derive(PerfectClone)]
enum List<T> {
    Empty,
    Cons(T, Box<List<T>>),
}

// would generate:

impl<T> Clone for List<T>
where
    T: Clone,
    Box<List<T>>: Clone,
{ ... }
```

This is a desirable feature however, so we'd like to allow this.
Let's make the cycle explicit:

```rust
impl<T> "clone_box_impl" Clone for Box<T>
where
    T: Clone,
{ ... }

impl<T> "clone_list_impl" Clone for List<T>
where
    T: Clone,
    Box<List<T>>: Clone,
{ ... }

// The cycle only appears when we make use of the impl:
fn clone_list<T>(l: &List<T>) -> List<T>
where
    t_clone: [T: Clone]
{
    recursively(|proof|
        clone_list_impl<T>[t_clone, clone_box_impl<T>[proof]]
    )::clone(l)
}
```

This is recursive (the technical term we use is "coinductive"), but this one poses no soundness problem.
We could choose to allow the impl above.

### Unsafe coinductive impls

The issue with the example above is that it puts into question what a `T: Trait` bound even means:
in the world where trait predicates are understood to be logical facts, something like:
```rust
impl<T> Clone for List<T>
where
    T: Clone,
    List<T>: Clone,
{ ... }
```
looks like nonsense: you can't assume a proposition to be true in the process of proving it.

This is particularly stark for unsafe traits:
```rust
unsafe impl<T> UnsafeTrait for MyPtr<T>
where
    MyPtr<T>: UnsafeTrait
{ ... }
```

Safety comments typically reason in terms of logical facts:
"I am given a type that implements all these traits, therefore such invariant holds,
therefore this unsafe impl upholds its safety requirements".

If we allow impls to be coinductive as proposed in the previous section,
then a situation like the following could arise:

```rust
pub struct MyOtherPtr<T>(MyPtr<T>);

/// Safety: `MyPtr` and `MyOtherPtr` are the same thing so this impl is ok.
unsafe impl<T> UnsafeTrait for MyPtr<T>
where
    MyOtherPtr<T>: UnsafeTrait
{ ... }

/// Safety: `MyOtherPtr` and `MyPtr` are the same thing so this impl is ok.
unsafe impl<T> UnsafeTrait for MyOtherPtr<T>
where
    MyPtr<T>: UnsafeTrait
{ ... }
```

These two impls look individually fine, but together they're nonsense.
I don't fully understand the situation there
but it seems to me that in a world of traits-via-proof-objects,
unsafe authors would have to check their unsafe justifications
for bad cycles by hand.
I.e. rustc would allow the above impls[^7], maybe with a warning,
and it would be on the unsafe code authors to make sure
that such a mutual dependency doesn't occur.

That problem is in fact more general: there's a tension between "trait bounds as propositions"
(more intuitive)
and "trait bounds as proof-relevant values" (more expressive)
that we'll have to resolve with a clear story.

[^7]: This is in fact [already allowed](https://play.rust-lang.org/?version=stable&mode=debug&edition=2024&gist=967f6896db8c603fd77cc32aaaec9ff6) for traits like `Send`: marker traits are allowed to be coinductive today.

### Infinite towers of impls

```rust
trait Trait {
    type Type: Trait;
}
impl<T> Trait for T {
    type Type = Box<T>; // This relies on itself to justify itself
}

// Explicitly:

trait Trait {
    type Type;
    // made-up syntax for an "associated proof"
    proof type_impls_trait: [Self::Type: Trait];
}
impl<T> "the_impl" Trait for T {
    type Type = Box<T>;
    // Here the recursive dependency is made explicit:
    proof type_impls_trait: [Box<T>: Trait] = the_impl<Box<T>>;
}
```

Any impl of this trait is a sort of infinite tower: `T::Type::Type::...::Type` is always defined.

Despite this infiniteness, the impl above does not appear to pose a soundness problem; the argument
for why is something like "well every finite question we could ask of it has a well-defined answer".

### Cycles through GATs

```rust
trait Trait {
    type Type1;
    type Type2;
}

trait WithGAT {
    type GAT<X: Trait>;
}

impl<T: WithGAT> Trait for T {
    type Type1 = u32;
    type Type2 = T::GAT<Self>;
}
```

Whether this cycle is ok depends on what `T::GAT` does with `Self`.
If it accesses `Self::Type2` in some fashion, then the cycle is bad, e.g.:

```rust
impl WithGAT for () {
    type GAT<X: Trait> = X::Type2;
}

// Then this type is not well-defined:
<() as Trait>::Type2
```


### Cyclic trait justifying itself

Here is a coinductive case that should not be allowed:
```rust
trait Copy {}
trait Magic: Copy {}
impl<T: Magic> Magic for T {}

fn is_copy<T: Copy>() {}
fn copy_via_magic<T: Magic>() {
    is_copy::<T>()
}
fn main() {
    copy_via_magic::<String>()
}

// Explicitly:

trait Copy {}
trait Magic {
    proof self_copy: [Self: Copy];
}
impl<T> "magic_impl" Magic for T
where
    t_magic: [T: Magic]
{
    proof self_copy: [Self: Copy] = t_magic::self_copy;
}

fn is_copy<T>()
where
    t_copy: [T: Copy]
{}
fn copy_via_magic<T>()
where
    t_magic: [T: Magic]
{
    is_copy::<T>[t_magic::self_copy]()
}
fn main() {
    copy_via_magic::<String>[recursively(|proof| magic_impl<String>[proof])]()
}
```

This is obviously wrong since if allowed it would prove `String: Copy`.

The way this decomposes is: `magic_impl` uses one of its input proofs to justify `self_copy`; but
when we end up using `magic_impl`, we feed itself as that input, creating a cycle. Either of these
things separately could be fine, but together they form a bad cycle.

Like in our first coinductive example,
the cycle only appears when we use the impl.
Unlike then, that use leads to a circular definition;
the technical term is that this proof is "non-productive".
Whatever checker we implement should detect this and reject it.


### Equality proof justifying itself

Let's revisit [this example](https://github.com/rust-lang/rust/issues/135246)
that we mentioned in the introduction,
using a variant that's easier to understand:

```rust
trait Trait<R>: Sized {
    type Proof: Trait<R, Proof = Self>;
}

impl<L, R> Trait<R> for L
where
    L: Trait<R>,
    R: Trait<R, Proof = <L::Proof as Trait<R>>::Proof>,
{
    type Proof = R;
}
fn transmute_inner<L: Trait<R>, R>(r: L) -> <L::Proof as Trait<R>>::Proof { r }
fn transmute<L, R>(r: L) -> R { transmute_inner::<L, R>(r) }
```

With everything explicit, this becomes:

```rust
trait Trait<R> {
    proof self_sized: [Self: Sized],
    type Proof;
    proof item_bound: [Self::Proof: Trait<R>],
    proof item_bound_eq: Self::item_bound::Proof == Self,
}

impl<L, R> "the_impl" Trait<R> for L
where
    l_sized: [L: Sized],
    l_bound: [L: Trait<R>],
    r_bound: [R: Trait<R>],
    r_bound_eq: r_bound::Proof == l_bound::item_bound::Proof
{
    proof self_sized: [Self: Sized] = l_sized;
    type Proof = R;
    proof item_bound: [Self::Proof: Trait<R>] = r_bound;
    // Here we use `l_bound::item_bound_eq` to prove that the `item_bound_eq` of the impl holds. When we
    // later pass the impl itself as `l_bound`, that creates a bad cycle.
    proof item_bound_eq: (Self::item_bound::Proof == Self)
        = transitivity(r_bound_eq, l_bound::item_bound_eq);
}

fn transmute_inner<L, R>(r: L) -> l_bound::item_bound::Proof
where
    l_bound: [L: Trait<R>],
{
    // We have a proof of equality so we can safely transmute between these two equal types.
    symmetry(l_bound::item_bound_eq)::transmute(r)
}

fn transmute<L, R>(r: L) -> R {
    transmute_inner::<L, R>[
        // The impl depends on itself; we must therefore build a recursive proof object.
        recursively(|proof|
            the_impl<L, R>[
                proof,
                // We need to call the impl a second time to prove the `R: Trait<R>` bound.
                recursively(|r_proof| the_impl<R, R>[r_proof, r_proof, trivial_eq]),
                trivial_eq
            ]
        )
    ](r)
}
```

This example is similar to the previous one: the impl would be ok on its own,
but using it creates a bad cycle, that can in fact be exploited for
unsoundness today.

This is an interesting cycle because if we hadn't made the equality proofs explicit then we wouldn't
see a bad cycle: while the impl depends on itself, it wouldn't be using that dependency in any
interesting way so the cycle would be ok.
The equality proofs reveal where the bad cycle resides.


### Cycles involving well-formedness

```rust
trait Trait {
    type Assoc;
}

impl Trait for i32 {
    type Assoc = <u32 as Trait>::Assoc;
}

impl Trait for u32
where
    i32: Trait<Assoc = ()>,
{
    type Assoc = ();
}

// Explicitly:

trait Trait {
    type Assoc;
}

impl "i32_impl" Trait for i32 {
    type Assoc = u32_impl[i32_impl, trivial_eq]::Assoc;
}

impl "u32_impl" Trait for u32
where
    i32_proof: [i32: Trait],
    i32_assoc_eq: i32_proof::Assoc == (),
{
    type Assoc = ();
}
```

In this example, `trivial_eq` claims to prove `i32_impl::Assoc == ()`, i.e.
`u32_impl[i32_impl, trivial_eq]::Assoc == ()`, which we can observe is true but we aren't done
checking that this definition is even well-formed, so maybe we shouldn't use it yet?

This overflows the trait solver today as well as my brain, but I'm not sure that's a bad cycle.
I'll leave it here for you to ponder.


### Clauses that depend on themselves

This one is very fun: if the trait solver uses the local clause to justify itself,
we end up in a funky cycle.

```rust
trait Iterator {}

trait IntoIterator {
    type IntoIter: Iterator;
}

impl<T: Iterator> IntoIterator for T {
    type IntoIter = T;
}

fn test()
where
    <Vec<()> as IntoIterator>::IntoIter: Iterator,
{
}

// Explicitly:

trait Iterator {}

trait IntoIterator {
    type IntoIter;
    proof intoiter_is_iterator: [Self::IntoIter: Iterator];
}

impl<T> "iter_to_intoiter_impl" IntoIterator for T
where
    t_iter: [T: Iterator]
{
    type IntoIter = T;
    proof intoiter_is_iterator: [Self::IntoIter: Iterator] = t_iter;
}

// Note how this uses no proof about `Vec<T>: IntoIterator` and would work with any other type.
fn test()
where
    proof: [iter_to_intoiter_impl<Vec<()>>[proof]::IntoIter: Iterator]
{
}
```

This is even worse than the previous one; I hope we never have to accept such madness.


### Negative recursion

The technical term is "recursion in negative position".
This means something of the shape `struct Foo(fn(Foo) -> u32)`, i.e. a recursive item that refers to itself
in the arguments of a function type, or more generally in contravariant position.
This is a whole new kind of cycle that doesn't technically involve an item referring to itself
(and thus isn't caught by any checks we could have invented to catch the previous bad cycles):
```rust
trait Trait {}

trait NegRecursive {
    type Diverge<Recur: NegRecursive>: Trait;
}

impl NegRecursive for () {
    type Diverge<Recur: NegRecursive> = Recur::Diverge<Recur>;
}

// Explicitly:

trait Trait {}

trait NegRecursive {
    type Diverge<Recur>
    where recur_proof: [Recur: NegRecursive]
    ;

    // The associated proof too takes arguments!
    proof diverge_impl_trait<Recur>: [Self::Diverge<Recur>[recur_proof]: Trait]
    where recur_proof: [Recur: NegRecursive]
    ;
}

// This isn't directly cyclic: `unit_impl` doesn't mention itself.
// But this applies a thing to itself, which may complete a cycle.
impl "unit_impl" NegRecursive for () {
    type Diverge<Recur>
        where recur_proof: [Recur: NegRecursive]
        = recur_proof::Diverge<Recur>[recur_proof]
    ;

    proof diverge_impl_trait<Recur>: [Self::Diverge<Recur>[recur_proof]: Trait]
        where recur_proof: [Recur: NegRecursive]
        = recur_proof::diverge_impl_trait<Recur>[recur_proof]
    ;
}
```

This [can be exploited for
unsoundness](https://github.com/rust-lang/rust/issues/135011#issuecomment-2577585549).



## Conclusion and next steps

This explicitness about trait proofs has an amazing benefit:
we can "just" look at the shape of the interdependencies between trait/equality
proofs and find a criterion that rejects all the bad cycles[^3][^4].
Once we have that, it's not too hard to convince ourselves that our system is sound.

The drawback is that this view of traits is in some ways less intuitive.
As discussed in the section on unsafe traits,
it carries a risk of having bounds not mean what users
think they should, potentially causing
sane-looking unsafe code to be incorrect.
There's a needle to thread there, stay tuned on the [Project Goal
issue](https://github.com/rust-lang/rust-project-goals/issues/630)
for updates.

In the meantime, we'll keep exploring this avenue.
To explore this concretely I wrote a toy dependent lambda calculus with coinduction, that can
[express all these
examples](https://github.com/Nadrieril/dictionary-passing-lambda-calculus/blob/main/tests/traits.rs)
and rejects all the unsound ones. It's not quite sound itself tho yet, and rejects too much.

For the next steps, we want to make that toy language more robust
and experiment with implementing some of these checks inside rustc or a-mir-formality.

I'd like to thank lcnr for the numerous conversations on this topic,
for teaching me everything I know about trait cycles,
and for providing all the examples in this article for me to bang my head on.


[^3]: This will of course also reject some good cycles, because the problem we're trying to solve is termination of a program, which is [notoriously undecidable](https://en.wikipedia.org/wiki/Halting_problem).

[^4]: This is even a known problem: formally, we're trying to decide productivity of some coinductively defined items.
