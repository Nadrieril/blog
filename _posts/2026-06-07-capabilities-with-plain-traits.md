---
title: "Capabilities using Plain Traits"
date: 2026-06-07 19:37 +0200
---

In a [recent post][traits_values], I touched upon the ["contexts and
capabilities"][con_cat] feature idea; after more thought and exciting discussions with [Boxy],
[Jana] and others, there's a bullet we're tempted to bite: what if we just used plain old trait
bounds?

[traits_values]: https://nadrieril.github.io/blog/2026/03/22/what-if-traits-carried-values.html
[con_cat]: https://tmandry.gitlab.io/blog/posts/2021-12-21-context-capabilities
[Jana]: https://github.com/jdonszelmann
[Boxy]: https://github.com/boxyuwu


> This has surely been proposed many times before but I didn't find much on a quick search, if
> anyone knows a good presentation of similar ideas DM me so I can give credit.

> [!NOTE]
> Terminology note: I've been using "capabilities" to describe "something in the signature of
> a function that allows some operations within it". This is distinct from "contexts", which is the
> word I use for "implicit argument-passing". A capability is just a ZST context. Finally, both of
> these are special cases of "effects", which propagate similarly between functions but can also
> allow altering the control-flow.

## Conditional Compilation using Trivial Bounds

A "trivial bound" is a trait bound that mentions no generic parameter; these are allowed under
a nightly feature.
For my first trick, I'll use them to replace `#[cfg(..)]` annotations.

Imagine the standard library contained this:

```rust
#![feature(trivial_bounds)]
pub struct Env; // A normal struct

pub trait Unix {}
pub trait Windows {}
pub trait Linux: Unix {}

#[cfg(unix)]
impl Unix for Env {}
#[cfg(linux)]
impl Linux for Env {}
// etc

// In std::os::unix::fs
pub fn chown(...)
where
    Env: Unix // a normal trait bound
{ ... }
```

Now say I call `std::os::unix::fs::chown` in my crate. If I only compile on unix systems, things
will just work. If I compile for other targets too, I can write:
```rust
use core::capabilities::{Env, Unix};

fn unix_helper()
where Env: Unix
{
    // ...
    std::os::unix::fs::chown(...)?;
}
```
which just works: the trait solver is fine with assuming a trait bound that's blatantly not
yet implemented. The benefit: all the code gets typechecked, even the code that cannot run on the
current target.

### Boolean Operations

With `cfg`, we can do arbitrary boolean operations. Things are a bit different with capabilities.

- For `&&`, just use two bounds: `where Env: Unix, Env: PointerWidth<u32>`;
- For `||`, declare a [marker trait](https://doc.rust-lang.org/beta/unstable-book/language-features/marker-trait-attr.html):

    ```rust
    #[marker] // this allows impls to overlap
    trait LinuxOrWindows {}
    impl<T: Linux> LinuxOrWindows for T {}
    impl<T: Windows> LinuxOrWindows for T {}
    ```

- For negation, we have to think differently. Trait bounds are a positive notion, and while negative
  trait bounds have been discussed since the dawn of traits, I won't hold my breath for them.
  That's generally not a problem though: being "not on unix" doesn't allow new behaviors,
  a function with `#[cfg(not(something))]` would generally also compile without the annotation[^3].

[^3]: This isn't fully true: some std APIs use negation, e.g. `std::os::unix::fs::chroot` has `#[cfg(all(unix, not(target_os = "fuschia")))]`. The solution for these it to rephrase it as a positive capability: `trait HasChroot: Unix` or at worst `trait UnixNotFuschia: Unix`.

### Branching on Available Capabilities

So we come to branching: how do we replace something like the following?
```rust
#[cfg(windows)]
fn my_function() { ... }
#[cfg(not(windows))]
fn my_function() { ... }
```

The answer uses my favorite WIP trait feature: maybe bounds[^1].
In short: a `T: maybe Trait` bound is satisfied whether or not `T` implements `Trait` in the calling
environment, and the callee can branch depending on whether it does[^2].

The function above becomes:
```rust
fn my_function()
where Env: maybe Windows
{
    if is_implemented!(Env: Windows) { // magic syntax :3
        // In this scope, the compiler knows that `Env: Windows`!
        windows_api()
    } else {
        fallback_api()
    }
}
```

Of course you don't want to have to write this bound everywhere.
Here's how the standard library can declare this bound to be always available:
```rust
pub trait Environment: maybe Windows + maybe Unix + ... {}
impl Environment for Env {}
```

What this impl does is that knowledge of `Env: Environment` (which is justified by the impl) is
enough to be allowed to ask whether `Env: Windows`. So now any function can ask
whether `is_implemented!(Env: Windows)`:
our function doesn't even need the `Env: maybe Windows` bound.

[^1]: Recently described [here](https://lcnr.de/blog/2026/03/06/always-applicable.html) by @lcnr, but proposed before, e.g. [here](https://internals.rust-lang.org/t/idea-maybe-trait-object-and-bounds-an-alternative-form-of-specialization/18176).

[^2]: This is a lightweight form of [specialization](https://aturon.github.io/blog/2017/07/08/lifetime-dispatch/).

## Target Features

Many CPU architectures include optional instructions that may not be implemented by all CPUs, e.g.
SIMD stuff.
To make use of these special instructions, Rust has a mechanism called ["target
features"](https://rust-lang.github.io/rfcs/2045-target-feature.html) to track at runtime and
compile time whether a given set of instructions can be used.
This fits quite well in our traits model.

The main part of this feature is that a function can opt-in to being compiled with a specific
extended instruction set:
```rust
#[target_feature(enable = "avx")]
unsafe fn times_two_avx(v: &mut [f64]) {
    for v in v {
        *v *= 2.0;
    }
}
```
This function is unsafe to call because today Rust has no mechanism to prevent you from calling that
function on the wrong target. There have been proposals, most notably
[`struct_target_features`](https://github.com/rust-lang/rfcs/pull/3525), to force the user to do it
right.
This section is an alternative proposal.

I propose, as you might expect, to write that function as follows:
```rust
fn times_two_avx(v: &mut [f64])
where Env: Avx
{
    for v in v {
        *v *= 2.0;
    }
}
```

How does that work? `impl Mul for f64` would be changed to look like:
```rust
impl Mul for f64
where Env: maybe Avx + maybe Avx512f + ...
{
    type Output = f64;
    fn mul(self, rhs: f64) -> Self::Output {
        if is_implemented!(Env: Avx) {
            ...
        } else {
            ...
        }
    }
}
```

By the very way maybe bounds work, making use of this method in a function with a `where Env: Avx`
will cause the impl to know `Avx` is available, which allows it to call a different instruction/intrinsic
that the codegen backend can then vectorize using the special instructions.

In other words, the trait bound is not magic, only the selected instruction/intrinsic carries the
knowledge of the available target feature(s)[^4].

[^4]: Well, this is a cute model but it probably breaks down in a bunch of ways. Worst case we can make the trait bounds magic and have the same meaning as the attributes do today. Also I haven't thought about ABI-altering target features; could that be a maybe bound on the type decl, that would prevent equating `f64` with `f64 where Env: SomeFeature`? I don't know.

### Runtime-Dependent Trait Bounds?

Now, the main point of this feature is that we don't know at compile-time which CPU the binary will
be running on. So unlike for `cfg` above, there won't ever be an `impl Avx for Env`[^5].

So then, starting from a `fn main() { ... }` that doesn't have any maybe bounds, how do we even get
to call a function with `Env: Avx`?
Well, with a magic macro again:
```rust
if is_x86_feature_detected!("avx") {
    // In this scope, `Env: Avx` holds
}
```

This would use the same kind of magic that maybe bounds use for control-flow dependent trait
bounds. Maybe this expands to something like `if builtin_is_x86_feature_detected!("avx") && unsafe
{ assert_implemented_unchecked!(Env: Avx) }`.

[^5]: Actually `#[cfg(target_feature = ...)]` does exist, for when we force compilation for a specific instruction set, so we would materialize an impl then. But in the general case there may not be one.

### The Inline-Reuse trick

Today, a typical use of the feature will define the core computation once and make feature-specific
wrappers around it:
```rust
#[inline(always)]
fn times_two_generic(v: &mut [f64]) {
    for v in v {
        *v *= 2.0;
    }
}

#[target_feature(enable = "avx")]
unsafe fn times_two_avx(v: &mut [f64]) {
    times_two_generic(v);
}

#[target_feature(enable = "avx512f")]
unsafe fn times_two_avx512f(v: &mut [f64]) {
    times_two_generic(v);
}

pub fn times_two(v: &mut[f64]) {
    if is_x86_feature_detected!("avx512f") {
        times_two_avx512f(v);
    } else if is_x86_feature_detected!("avx") {
        times_two_avx(v);
    } else {
        times_two_generic(v);
    }
}
```
This works because inlining a function into a scope with more features allows it to make use of the
extra features, and the `inline(always)` forces the code of `times_two_generic` to be codegenned
twice, once inside each wrapper.

With our new capabilities, this looks like[^12]:
```rust
fn times_two_generic(v: &mut [f64])
where
    Env: maybe Avx + maybe Avx512f
{
    for v in v {
        *v *= 2.0;
    }
}

pub fn times_two(v: &mut[f64]) {
    if is_x86_feature_detected!("avx512f") {
        times_two_generic(v);
    } else if is_x86_feature_detected!("avx") {
        times_two_generic(v);
    } else {
        times_two_generic(v);
    }
}
```

The wrappers aren't needed here: a maybe bound acts like a `const IS_IMPLEMENTED: bool`
generic argument; the function will be recompiled for each value of it, i.e. for each variant of
which feature is enabled or not.
In particular, we no longer rely on inlining[^7].

<!-- Two drawbacks of the above: we have to list all the possible features on `times_two_generic`, and we -->
<!-- could accidentally call `times_two_generic` in an empty environment, which would make use of no -->
<!-- features and not be what the user expects. -->
<!-- With a little bit more work, I think the ideal version could look like: -->
<!-- ```rust -->
<!-- // Note that this is not implemented by `Env` (as that would imply we can decide which feature is -->
<!-- // available at compile-time). -->
<!-- trait TargetFeatures: maybe Avx + maybe Avx512f + ... {} -->

<!-- // The `Env: TargetFeatures` bound prevents this function from accidentally being -->
<!-- // called in an empty environment, and makes it possible to use all existing -->
<!-- // target features. -->
<!-- fn times_two_generic(v: &mut [f64]) -->
<!-- where -->
<!--     Env: TargetFeatures -->
<!-- { -->
<!--     for v in v { -->
<!--         *v *= 2.0; -->
<!--     } -->
<!-- } -->

<!-- pub fn times_two(v: &mut[f64]) { -->
<!--     // Each macro also introduces `Env: TargetFeatures` now. -->
<!--     if is_x86_feature_detected!("avx512f") { -->
<!--         times_two_generic(v); -->
<!--     } else if is_x86_feature_detected!("avx") { -->
<!--         times_two_generic(v); -->
<!--     } else { -->
<!--         no_target_feature!(); // Only introduces `Env: TargetFeatures` into the scope -->
<!--         times_two_generic(v); -->
<!--     } -->
<!-- } -->
<!-- ``` -->

<!-- The one thing we can't avoid is having many call sites of the same function, because each -->
<!-- corresponds to a different choice of features which will be monomorphized as a separate function. -->

[^12]: In practice we wouldn't need to list all the capabilities: std could have a trait alias `trait TargetFeatures: maybe Avx + maybe Avx512f + ... {}` that does list them all, and then a function that wishes to use any target features would only need `where Env: TargetFeatures`.

[^7]: You'll note the striking similarity with the struct_target_feature proposal. I'd say they're in fact conceptually the same, except that with capabilities we can pass the special struct implicitly across function calls. See also [this blog post][traits_values] for more on the idea of trait bounds carrying implicit values.

## Tracking Whether a Function Can Unwind

Let us now imagine that the `start_panic` intrinsic takes `where Env: Unwind`
(when built with `panic=unwind`).
To avoid breaking the world, we'll also say that every standalone function as well as every trait and
trait impl gets an implicit `where Env: Unwind` bound.

We can now write:
```rust
fn doesnt_panic() -> u32
where
    Env: ?Unwind
{
    42
}
```

Because we cannot prove `Env: Unwind` inside this function, calling this function cannot unwind.
A caller of this function would be able to skip codegenning an unwind path.
An unsafe caller of this function would be able to rely on not-unwinding to make
their unsafe code easier to write.

We could imagine having special syntax like `nounwind fn` for that, if we wanted.
However note that the story gets subtler in the presence of generics, so `nounwind` could be
a misleading name, see next section.

### Functions Are Capability-Generic By Default

The natural question with any sort of "effect"-like system such as this is: how does this compose?
Our answer is: capabilities flow through trait bounds.

Take this standard library function:
```rust
impl<T> Option<T> {
    pub fn map<F, U>(self, f: F) -> Option<U>
    where
        Env: ?Unwind,
        F: FnOnce(T) -> U
    { ... }
}
```

The `Env: ?Unwind` bound prevents the body of `map` from proving `Env: Unwind`, so it cannot call
`panic` or `unwrap` or any function that requires that bound. But watch what happens when we pass it
a closure:
```rust
let x = None;
let y = x.map(|_| panic!());
```

Here the closure type implements `FnOnce`, and the impl would look like
```rust
impl FnOnce for closure
where Env: Unwind
{ ... }
```

Nothing in the signature of `map` prevents this.
What happens instead is that, in order to know whether a given function call
can unwind, we need to check all of its trait clauses.
If proving any of the clauses used a `Env: Unwind` bound somewhere, then the call may unwind[^8].

That's why I find the `nounwind fn` syntax misleading: this seems to say that the
function can never unwind, whereas `Env: ?Unwind` only means "this function cannot unwind *by
itself*"[^9].

We can however know that `opt.map(|x| (x, 0))` won't unwind.

> [!NOTE]
> Or, well, I hope we can, but I still haven't figured out how the compiler would decide what `Env`
> bounds to add to the automatic `impl FnOnce` for a closure.
> If it emits a naive `impl FnOnce`, the implicit `Env: Unwind` bound means all closures are assumed to
> panic. Maybe these traits get a special annotation so that they're propagated onto `impl FnOnce`
> impls? At worst, we'll need a special syntax like `opt.map(with(Env: ?Unwind) |x| (x, 0))` (see the
> last section for a slightly less ugly syntax option).

[^8]: This works very nicely if we [construct explicit proofs for all trait facts](https://nadrieril.github.io/blog/2026/03/20/dictionary-passing-style.html): then you only have to walk the proof, looking for a proof of `Env: Unwind` being used.

[^9]: Even that is imprecise: if we define `trait MyUnwind: Unwind {}`, a function with `where Env: MyUnwind + ?Unwind` can perfectly well call `panic!()` directly.

### Negative Capability

With the above, we can sometimes tell that a given function call cannot unwind.
What would be also very useful would be to say "give me a closure that cannot unwind".
For this we need a new idea:
```rust
fn foo<F>(f: F)
where
    F: (FnOnce() -> u32) without (Env: Unwind)
{ ... }
```

I'm stretching the imagination of trait-related features here, but this is rather simple to explain:
this function accepts an `F` if proving its `FnOnce` bound did not make use of any `Env: Unwind`
bound.

In the world where we [construct explicit proofs for all trait
facts](https://nadrieril.github.io/blog/2026/03/20/dictionary-passing-style.html),
this says: "give me a proof of `F: FnOnce() -> u32` that doesn't use any proof of `Env:
Unwind`". Such a closure necessarily cannot panic.

> [!NOTE]
> I've been using closures as examples but this works with any trait, e.g. `T: Clone
> without (Env: Unwind)`.

### The Signature of `catch_unwind`

There remains one mystery to complete this picture: how to write `catch_unwind`.
For that I'll pull a final trait solver feature out of my hat: implication bounds[^11].

```rust
pub fn catch_unwind<F, R>(f: F) -> Result<R, Box<dyn Any>>
where
    Env: ?Unwind,
    (Env: Unwind) => (F: FnOnce() -> R)
{ ... }
```

When calling this function with a particular `F`, the trait solver tries as usual to prove that
`F: FnOnce() -> R`. The difference is that on top of the things it is normally allowed to assume in
the caller context, it may also assume `Env: Unwind`.

When determining whether a given call to `catch_unwind` may itself unwind, the trait solver will
then always find that none of the trait proofs use a `Env: Unwind` coming from "outside", which is
our criterion for knowing that the call cannot unwind.

As for the body of `catch_unwind`, it might look something like:
```rust
unsafe {
    assert_implemented_unchecked!(Env: Unwind);
    // Here `F: FnOnce() -> R` holds for real.
    actually_catch_unwind(f) // does the real work
}
```

<!-- Let's step through what the trait solver does: -->
<!-- - The closure becomes a new struct, that carries the `&mut array`; -->
<!-- - An `impl FnOnce` is written for it, whose `call_once` contains the actual code for the closure; -->
<!-- - Somehow the compiler decides to add a `where Env: Avx` bound to that `impl FnOnce`. This is the -->
<!-- - When we call `select_avx` with the closure, the trait solver tries to prove `impl FnOnce` for it, -->
<!--   is missing an `Env: Avx` in the context, and gets it from the implication bound premise instead. -->
<!--   All compiles. -->

<!-- ### Unfortunately, Panicking Is Pervasive -->

<!-- One large limitation of any `nopanic`/`nounwind` feature is the simple fact that some very basic -->
<!-- Rust operations can panic. Most notably arithmetic operations, indexing, and allocation. -->

[^11]: The full feature is probably a huge thing, but what I need for this only has marker traits with no generics to the left of the arrow, which I hope is simple enough to actually get it.

## `const` Traits In This Model

`const`, as in `const fn`, is much like `nounwind`: it's a "negative capability", or rather the
removal of a capability that's there by default.
We can reuse all the ideas we've seen to express this in our framework:
we'll call the capability "`Runtime`", and add it by default on toplevel functions, traits, and
trait impls.

```rust
/// `Env: Runtime` gives access to interaction with the os, filesystem, etc.
/// Everything that `const fn`s cannot do.
pub trait Runtime {}

// Has implicit `Env: Runtime`
fn foo() { ... }

// Has no implicit `Env: Runtime`
const fn foo() { ... }
// is that same as:
fn foo() where Env: ?Runtime { ... }

// Has an implicit `Env: Runtime`
trait Clone: Sized { ... }

// Has no implicit `Env: Runtime`
const trait Clone: Sized { ... }
// is the same as:
trait Clone: Sized
where
    Env: ?Runtime
{ ... }

// Has an implicit `Env: Runtime`
impl Clone for Foo { ... }

// Has no implicit `Env: Runtime`
const impl Clone for Foo { ... }
// is the same as:
impl Clone for Foo
where
    Env: ?Runtime
{ ... }
```

This is a rather straightforward translation; the two views are rather concordant.
<!-- The way to understand `const trait` in this view is: a `const trait` is a trait that does not -->
<!-- require a runtime context. In particular, its methods can't assume one is present. -->
<!-- A `const impl` similarly is an impl that does not require a runtime context. It's not legal to make -->
<!-- a `const impl` for a non-`const` `trait` because it could not prove the required `Env: Runtime` -->
<!-- bound on the trait. -->

I'm a bit out of touch with the latest state of the `const` trait syntax discussions, but if I recall
one of the syntax options was:
```rust
const fn foo<T>()
where
    T: Clone,
{ ... }
// is the same as:
fn foo<T>()
where
    Env: ?Runtime, // the function doesn't do any runtime ops...
    Env: Runtime => T: Clone, // ...even if the `T: Clone` does


const fn foo<T>()
where
    T: [const] Clone,
{ ... }
// is the same as:
fn foo<T>()
where
    Env: ?Runtime, // the function doesn't do runtime ops itself...
    T: Clone, // ...but could propagate those of the `Clone` impl


const fn foo<T>()
where
    T: const Clone,
{ ... }
// is the same as:
fn foo<T>()
where
    Env: ?Runtime, // the function doesn't do runtime ops itself...
    T: Clone without (Env: Runtime), // ...and the `Clone` impl cannot either
```

This can express everything that the `const trait` proposal includes, and even more.
This may be a drawback, e.g. it's possible to express "a method that can do runtime stuff" inside
a `const trait`, and even `maybe Runtime` bounds i.e. functions that behave differently depending
on whether they're in a runtime context or not[^10].

[^10]: To be very precise, maybe bounds cannot learn more than what the caller knows. It thus would be possible to call the `const`-version of that function at runtime, by calling it from an intermediate `const fn`. In that way `maybe Runtime` is not equivalent to `const_eval_select`.

<!-- ## Maybe Bounds at Home Using `try_as_dyn`? -->

<!-- Maybe bounds aren't quite being worked on at the moment, but there's a related feature that can get -->
<!-- us part of the way there: [`try_as_dyn`](https://doc.rust-lang.org/nightly/core/any/fn.try_as_dyn.html). -->

<!-- ```rust -->
<!-- #![feature(trivial_bounds)] -->
<!-- #![feature(try_as_dyn)] -->

<!-- pub struct Env; -->
<!-- pub trait Unix {} -->

<!-- #[cfg(unix)] -->
<!-- impl Unix for Env {} -->

<!-- fn unix_only() -->
<!-- where Env: Unix -->
<!-- { -->
<!--     println!("unix!") -->
<!-- } -->

<!-- // We represent the runtime knowledge of `Env: Unix` as a `&dyn Unix`. In order to use that bound, -->
<!-- // we define our own alias and use `dyn MyTrait` as witness. -->
<!-- trait MyTrait: Unix { -->
<!--     fn call_unix_only(&self); -->
<!-- } -->
<!-- impl MyTrait for Env -->
<!-- where -->
<!--     Env: Unix -->
<!-- { -->
<!--     fn call_unix_only(&self) { -->
<!--         unix_only() -->
<!--     } -->
<!-- } -->

<!-- fn main() { -->
<!--     if let Some(witness) = core::any::try_as_dyn::<_, dyn MyTrait>(&Env) { -->
<!--         witness.call_unix_only() -->
<!--     } else { -->
<!--         println!("not unix!") -->
<!--     } -->
<!-- } -->
<!-- ``` -->

<!-- That's enough for the `cfg` stuff but not for more, as the property of maybe bounds picking stuff up -->
<!-- from their environment is crucial for the other features. -->

## Bonus Fun Ideas

### Selfless traits

This dummy `Env` type is rather useless. We could imagine traits that don't have a `Self` type:
```rust
#[selfless]
pub trait Unix {}

#[cfg(unix)]
impl Unix {}
```

Here's the various syntaxes we introduced, if the traits become selfless:
```rust
pub fn chown(...)
where Unix
{ ... }

fn my_function()
where maybe Windows
{
    if is_implemented!(Windows) {
        ...
    }
}

fn doesnt_panic() -> u32
where ?Unwind
{
    42
}

fn foo<F>(f: F)
where
    F: (FnOnce() -> u32) without Unwind
{ ... }

pub fn catch_unwind<F, R>(f: F) -> Result<R, Box<dyn Any>>
where
    ?Unwind,
    Unwind => (F: FnOnce() -> R)
{ ... }

opt.map(with(?Unwind) |x| (x, 0))
```

I think that's cute.

### Precise Capabilities in the Standard Library

These capabilities are just traits, we can define however many we want!
Here's what we could have in the standard library:

```rust
/// Allows unwinding.
trait Unwind {}

/// Allows loops that many not terminate, i.e. over iterators that don't implement
/// some `unsafe trait FiniteIterator`
trait NonTermination {}

/// Allows access to non-deterministic APIs.
trait NonDet {}

/// Allows reading/writing to file/network/etc.
trait Io: NonDet {}

/// Allows using the global allocator.
trait Alloc {}

/// Allows all interactions with the runtime (e.g. cpu info, filesystem access).
trait Runtime: NonTermination + Io + Alloc {}

/// Allows interacting with the compile-time (e.g. reflection, type info).
trait CompileTime {}

/// All of the platform `cfg`s we talked about.
trait Unix: Io {}
```

Uh-oh we got a bit of an effect system, haven't we :3

### Moar Effects

All of these cool trait features work equally well if [traits can carry values][traits_values].
That gives us what I've been calling "contexts", and [Jana] has a cool idea for how this can
be made a lot more non-breaking than I wrote in my blog post.

The final frontier then, is control-flow-affecting effects.
I and others smell that the same trait-bound-based story could get us there.
Stay tuned for more blog posts.

## Conclusion

I hope this little exploration has convinced you of how damn flexible and powerful things can get when
we piggy-back on traits.
By and large the enabling feature was maybe bounds; I hope we get them in the language!

This was a dense blog post, and I didn't spend a lot of time explaining the advanced
features I use.
Sorry :D
Ask below if you'd like some specific clarification! You can also DM me on the rust-lang Zulip.
