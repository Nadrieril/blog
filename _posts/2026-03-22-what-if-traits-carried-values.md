---
title: "What If Traits Carried Values"
date: 2026-03-22 07:30 +0100
---

In my [last post](https://nadrieril.github.io/blog/2026/03/20/dictionary-passing-style.html),
I showed you how traits behave like passing a bundle of methods between functions,
except automatically inferred by the compiler.

[Tyler Mandry](https://github.com/tmandry) [was quick to point
out](https://rust-lang.zulipchat.com/#narrow/channel/144729-t-types/topic/A.20calculus.20for.20dictionary-passing-style/near/580762824)
that this looks just like
[contexts/capabilities](https://tmandry.gitlab.io/blog/posts/2021-12-21-context-capabilities).

In this post I'll explore the underlying question: what if trait bounds also carried values?

## Contexts and Capabilities

We'll start with a Rust feature idea I've been giddy about since I came across it, well-presented by
Tyler in [his blog post](https://tmandry.gitlab.io/blog/posts/2021-12-21-context-capabilities).
I recommend giving it a read, but I'll summarize the core idea.

The feature has three components:

1. You declare a global name with `capability my_capability;`;
2. You can now write `my_capability: Type` in a `where` bound, and this works like an implicit argument:
   the compiler will pass you a value and error if it can't find one;
3. A value is provided for a given scope by writing: `with my_capability = some_value() { ... }`.

This is particularly awesome in trait impls:

```rust
capability arena;

impl<'a> Deserialize for &'a Foo
where
    arena: &'a BasicArena,
{
    ...
}

fn main() -> Result<(), Error> {
    let bytes = read_some_bytes()?;
    with arena = &arena::BasicArena::new() {
        let foos: Vec<&Foo> = deserialize(bytes)?;
        println!("foos: {:?}", foos);
    }
    Ok(())
}
```

What happens here is that the dictionary[^3] for `<&Foo as Deserialize>` now also carries a runtime
value.
The compiler threads it through any intermediate functions/impls that have a `T: Deserialize` bound,
without them needing to know about it (well, see next section).

[^3]: See my [last post](https://nadrieril.github.io/blog/2026/03/20/dictionary-passing-style.html)

## Dictionaries carry values now

Implicitly threading values between unsuspecting functions does change things a bit, of course.

### Controlling the implicit value

As with any Rust generics, we'll need a way to control a bit which values we can support.
Tyler proposes:
```rust
fn deserialize_and_print_later<T>(deserializer: &mut Deserializer)
where with('static + Send) T: Deserialize
{ ... }
```
In our dictionary world, we may just as well write:
```rust
fn deserialize_and_print_later<T>(deserializer: &mut Deserializer)
where
    T: Deserialize,
    <T as Deserialize>: 'static + Send,
{ ... }
```

where `<T as Deserialize>` is understood to refer to the dictionary itself,
so we may apply bounds on it like any other type[^1].

[^1]: I'm fudging the difference between types and values here, but I don't think there's ambiguity in practice. A more precise way of doing this would be a magic associated type `<T as Deserialize>::capabilities: 'static + Send`.

### Linearity

Perhaps the craziest consequence of taking this seriously is that
trait bounds need ownership semantics now.
Imagine:

```rust
impl<'a> Deserialize for &'a Foo
where
    arena: &'a mut Vec<Foo>,
{
    ...
}
```

Now the dictionary contains a `&mut`, so we better be careful not to pass it to two functions at
once!
A function like the following cannot work on our `&Foo`:
```rust
fn bar<T: Deserialize>(bytes: Vec<u8>) {
    // The iterator needs to capture the `&mut` context.
    for item in whatever(bytes).map(|x| T::deserialize(x)) {
        // Trying to use it here too is an aliasing violation.
        let other_item = T::deserialize(something_else(bytes));
        ...
    }
}
```

We therefore need to distinguish the 4 kinds of ownership semantics we can encounter:
- Today's default, with no implicit value at all: `<T: Trait>: const`[^2];
- `&Context`-like semantics: `<T: Trait>: Copy`;
- `&mut Context`-like semantics: `<T: Trait>: Reborrow` (using the `Reborrow` trait from [the
  project goal](https://github.com/rust-lang/rust-project-goals/issues/399));
- `Box<Context>`-like semantics: `<T: Trait>` can contain anything.

You'll have recognized the similarity with the 4 closure traits [`FnStatic`](https://github.com/rust-lang/rust/issues/148768),
`Fn`, `FnMut`, and `FnOnce`.

Oh and for the owned case, trait bounds can have significant `Drop` if not used :3

This is unhinged enough that I'd propose we just limit contexts to being `Copy` and use
interior mutability,
but I suspect some delicious APIs could be cooked with the full expressivity 👀.
Plz share in the comments I wanna see them.

[^2]: Basically "is fully known at compile-time", so there's no need to thread any value. That's very different from `T: const Trait` which would mean "its methods can be called at compile-time". We'll probably not use such a similar notation, that would be confusing af x)

### Methods are closures

Speaking of closure traits,
methods are closures now:
in our example `<T as Deserialize>::deserialize` makes use of the implicit parameter,
so it cannot be cast to a `fn(D) -> Result<.., ..>` function pointer.

Depending on the ownership semantics of `<T as Deserialize>`,
its methods will implement the corresponding `Fn*` closure trait(s) instead.

### Scoped impls

As Tyler points out in his blog post,
trait bounds can no longer be taken to be global facts!
Depending on which capabilities are in scope, the same `MyType: Trait` may
or may hold.

This is a surprisingly expressive new capability,
especially if we stretch the feature set a bit:

```rust
struct MagicPointer<'a>(PhantomData<&'a ()>);

capability pointer_target;

// This `Deref` impl is only available when the capability is in scope,
// and it has a different target type depending on scope!
impl<'a> Deref for MagicPointer<'a>
where
    pointer_target: impl Sized + 'a // Can be basically anything
{
    type Target = type_of!(pointer_target); // I cheat, don't tell
    fn deref(&self) -> &Self::Target {
        &pointer_target
    }
}
```

This has far-reaching consequences on how we use traits:
```rust
struct MyInt(u32);

capability salt;

// This impl is correct inside a given context. But switching contexts breaks it:
// two equal values may hash differently in different contexts.
impl Hash for MyInt
where
    salt: u32
{
    ... // hash `self.0.xor(salt)`
}

fn main() {
    let mut set: HashSet<MyInt> = Default::default();
    with salt = 42 {
        set.insert(0);
    }
    with salt = 10 {
        if set.contains(&0) {
            // completely not clear whether that's the case.
            // depends on impl details
        }
    }
}
```

Either the `Hash` impl above is deemed invalid (seems likely),
or datastructures like `HashSet` would not opt-in to scoped impls.
Either way, this opens up a new dimension of expressivity.

## Capturing impls

I introduced the article with context/capabilities, but
this is not the only way I can think of to make trait bounds
carry values.
The other one is to have impls capture from their context!

For this, I'll reuse the idea of [`move`
expressions](https://smallcultfollowing.com/babysteps/blog/2025/11/21/move-expressions),
except I prefer to call them `capture` expressions.
We'll also still need a notion of scoped impls, I'll write that `local impl`.

```rust
struct Context;

trait GimmeArena {
    // The crazy lifetime syntax would mean "borrows from the trait dictionary".
    fn gimme() -> &'{Self as GimmeArena} Arena;
}

fn use_arena()
where
    Context: GimmeArena
{
    let arena = Context::gimme();
    // use the arena
}

fn foo() {
    let arena = Arena;

    local impl GimmeArena for Context {
        fn gimme() -> &'{Self as GimmeArena} Arena {
            capture(&arena)
        }
    }

    // In this scope, `Context` implements `GimmeArena`, and the dictionary
    // carries a reference to the arena.
    use_arena();
}

fn bar() {
    // Different scope, so we can make another impl.
    local impl GimmeArena for Context {
        fn gimme() -> &'{Self as GimmeArena} Arena {
            ... // do something else
        }
    }

    use_arena();
}
```

Feature-wise, this is pretty similar: `where Context: GimmeArena` is very close to
`where arena: &'a Arena` from before.

This also clashes with another understanding of what a "capturing impl" might be,
namely where the captured values are accessible from the `&mut self` argument,
which would allow conveniently defining `Iterator`s, visitors etc.

All in all I'm not too sold on this;
I'm showing it because it's a good illustration that the important
notion either way is data-carrying impls.

## Conclusion

I hope I got you excited about capabilities, and/or about trait-bounds-as-values!
What I find compelling is how naturally
"trait bounds carry runtime values"
interacts with the rest of the language.

I'll see you later for more explorations
of dictionary-passing-style traits.
