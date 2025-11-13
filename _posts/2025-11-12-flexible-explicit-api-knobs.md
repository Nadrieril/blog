---
title: "Flexible Explicit API Knobs"
date: 2025-11-12 10:43 +0100
---

A ton of t-lang design decisions hinge on thinking about "how can this code evolve", "what am I promising my users by writing this code", etc. And a lot of feature bikeshedding is about choosing sane defaults so crate authors know what they're committing to while still allowing flexibility.

I propose we could make this a more explicit part of the language by giving crate authors a common
language to control their present API surface and opt-in/opt-out of future API changes.

The proposed keyword choices are very much not what I expect to be accepted; I'm just trying to
share the idea for now. Please share other compelling examples! (I will edit this blog post to
include more examples).

```rust
// Equivalent to `#[non_exhaustive]` on enums.
#[semver_compatible(allow(author, add_variants)]
enum Enum { ... }

enum Enum {
    // Kinda opposite of `#[non_exhaustive]`; forbids user from matching or
    // constructing this variant.
    #[semver_compatible(allow(author, remove_variant)]
    Variant1,
    ..
}

// Equivalent to `#[non_exhaustive]` on structs.
#[semver_compatible(allow(author, add_fields)]
struct Struct { ... }

// The author can add fields but only public ones. This means downstream
// crates can use FRU (idea from scottmcm, ty!).
#[semver_compatible(allow(author, add_fields(pub))]
struct Struct { ... }

// The author can add fields but only with default values. This means downstream
// crates can construct it (idea from scottmcm, ty!).
#[semver_compatible(allow(author, add_fields(with_defaults))]
struct Struct { ... }

// Allows downstream crates to rely on the layout of this struct. Could be used
// for safe transmutation to reason about API/ABI stability.
#[semver_compatible(forbid(author, change_layout))]
#[repr(C)]
struct Struct { ... }

// Bound the size of the struct.
#[semver_compatible(size <= 42)]
struct Struct { ... }

// Commit to keeping these auto traits implemented.
#[semver_compatible(implements(Send))]
#[semver_compatible(implements(const Destruct))]
struct Struct { ... }

// Prevents downstream crates from implementing this trait. Basically builtin "sealed traits".
// This isn't a semver kind of thing so I picked another keyword but I
// don't like it much.
#[api(forbid(downstream, impl)]
trait Trait { ... }

// Ensures a trait is and stays dyn safe.
#[semver_compatible(is_dyn_safe)]
trait Trait { ... }

// Prevents adding a new method if it's not const.
// IIUC, should be enough to allow `const Trait` bounds. If so, that's an
// alternative to the `const trait Trait` syntax.
#[semver_compatible(forbid(author, add_method(not_const))]
trait Trait { ... }

trait Trait {
    // Prevents downstream crates from overriding this method. Replaces `final`
    // methods (https://github.com/rust-lang/rfcs/pull/3678).
    #[api(forbid(downstream, override)]
    fn method() { ... }
    
    // Prevents downstream crates from calling this method.
    #[api(forbid(downstream, call)]
    fn method() { ... }
}


// Ensures the lifetime/type param stays covariant.
#[semver_compatible(covariant('a))]
#[semver_compatible(covariant(T))]
struct Foo<'a, T> { ... }

// Prevents `foo::<...>` syntax as the generic parameters may change, e.g. going to `impl Trait` instead of an explicit param.
#[semver_compatible(allow(author, change_generics))]
fn foo<T: Trait>(..) { ... }

// Ensures the generated coroutine implements `Send`.
#[semver_compatible(implements(Send))]
async fn foo() { ... }
```

I would imagine, in a future edition, having a lint that warns if you're relying on un-committed-to
API facts, e.g. you use `dyn Trait` for a trait that didn't guarantee it would stay dyn-safe.

I feel like there are quite a number of features that would fit in this, and like we could find
a nice common language to talk about those things. This is a call for contributions: do you see
other examples?
