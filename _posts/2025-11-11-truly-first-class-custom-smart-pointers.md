---
title: "Truly First-Class Custom Smart Pointers"
date: 2025-11-11 15:55 +0100
---

> I propose this blog post as part of the discussions around the [Field Projections project
goal](https://github.com/rust-lang/rust-project-goals/issues/390). Thanks to Benno Lossin and
everyone involved for the very fruitful discussions!

What would it take to make custom smart pointers as first-class as possible in Rust? In this post I explore the consequences of taking aliasing seriously.

These reflections originated on
[Zulip](https://rust-lang.zulipchat.com/#narrow/channel/522311-t-lang.2Fcustom-refs/topic/Field.20projections.20and.20places/with/553831123)
and went over a few iterations. This is a snapshot of the proposal; it may keep evolving in this
[HackMD document](https://hackmd.io/N0sjLdl1S6C58UddR7Po5g). I've also made a repo to play with the
proposed traits [here](https://github.com/Nadrieril/place-projections-demo).

## The case for a solution based on places

In this section I explore the kind of code I think should be allowed and how this orients the feature design.

### `Deref` is insufficient

Assume a smart pointer `MyPtr` that implements `Deref` and `DerefMut`. The following causes a borrowck error:

```rust
let mut x: MyPtr<Foo>  = MyPtr::new(..);
let a = &mut x.a;
let b = &mut x.b;
*a = 0;
*b = 0;
```

This code is allowed if instead of `MyPtr<Foo>` we have `&mut Foo` or `Box<Foo>`, because these are special for the borrow-checker. Hence I want this for custom smart pointers too.

The desugaring shows why the error: the second call to `deref_mut` invalidates `a`.

```rust
// Desugars to:
let mut x: MyPtr<Foo> = MyPtr::new(..);
let deref_x: &mut Foo = (&mut x).deref_mut();
let a = &mut (*deref_x).a;
let deref_x: &mut Foo = (&mut x).deref_mut();
let b = &mut (*deref_x).b;
*a = 0;
*b = 0;
```

The conclusion I draw is: the meaning of `&mut x.a` should change/be extended.

### Projecting one field at a time is tricky

The basic desire of the Field Projections initiative is some way to go from a `MyPtr<Foo>` to
a `MyPtr<Field>` that points to a field of the original `Foo`.

There is an important question: can we project one field at a time? I.e. can `project!(x, field.a)`
be implemented as `project!(project!(x, field), a)`?

Possibly, but we have to be careful. Take the following example (using the `~` notation that is
under consideration for field projections):
```rust
#[make_projectable_somehow]
pub struct MyMutPtr<'a, T>(&'a mut T);

let my_ptr: MyMutPtr<Foo> = ...;
let a_ptr: MyMutPtr<A> = my_ptr~bar~a;
*a_ptr = ...;
let b_ptr: MyMutPtr<B> = my_ptr~bar~b;
*a_ptr = ...;
*b_ptr = ...;
```
in the obvious one-field-at-a-time desugaring, we'd get:
```rust
#[make_projectable_somehow]
pub struct MyMutPtr<'a, T>(&'a mut T);

let my_ptr: MyMutPtr<Foo> = ...;
let bar_ptr: MyMutPtr<A> = my_ptr~bar;
let a_ptr: MyMutPtr<A> = bar_ptr~a;
*a_ptr = ...; // this write activates the contained `&mut A`
let bar_ptr: MyMutPtr<A> = my_ptr~bar; // this reborrow invalidates `a_ptr`
let b_ptr: MyMutPtr<B> = bar_ptr~b;
*a_ptr = ...; // UB
*b_ptr = ...;
```

Which is UB under the currently proposed aliasing models, see [this
discussion](https://rust-lang.zulipchat.com/#narrow/channel/522311-t-lang.2Fcustom-refs/topic/Multi-level.20projections/near/555233842).

This is only a problem because we use `&mut` references though;
we could forbid projections of types like this,
or find workarounds using intermediate `*mut` pointers.

I propose instead: projection should work all at once;
we should not create intermediate pointer values when doing nested projections.

We discuss the field-by-field case in more depth
[here](https://rust-lang.zulipchat.com/#narrow/channel/522311-t-lang.2Fcustom-refs/topic/Field-by-field.20projections.20in.20the.20place.20model/with/554924231).

### Autoref

To be really first-class, autoref should Just Work:
```rust
impl Bar {
    fn takes_shared(&self) { ... }
    fn takes_mut(&mut self) { ... }
    fn takes_my_mut(self: MyMut<Self>) { ... }
}

let x: MyMut<'_, Foo> = ...;
x.bar.takes_shared();
x.bar.takes_mut();
x.bar.takes_my_mut();
```

From the previous section, we don't want to make a `MyMut` pointing to `x.bar` then coerce it to
`&Bar`; it should happen in one go.

The conclusion I draw is: a smart pointer should be able to opt-in to being projected into various other pointers. Something like:
```rust
trait Project<P: Projection, Target>
where
  Self: PointsTo<P::Source>,
  Target: PointsTo<P::Target>,
{
    fn project(*const self, p: P) -> Target;
}

// e.g., more or less (see below for a real proposal):
impl<T, U, P: Projection> Project<P, &U> for MyRefMut<'_, T> {...}
impl<T, U, P: Projection> Project<P, &mut U> for MyRefMut<'_, T> {...}
impl<T, U, P: Projection> Project<P, MyRefMut<'_, U>> for MyRefMut<'_, T> {...}
```

### Compositionality

Despite the point above that we shouldn't create intermediate pointer values, syntax should be compositional. `x.a.b` should be the same as `(x.a).b`, where the individual components make sense.

This already has a solution in Rust: place expressions. `x`, `*x`, `x.a` and `x.a.b` are expressions that denote a place.

The various part at play are: if `x: &mut T` and `foo` takes `&self`, `x.a.foo()` actually stands for `Foo::foo(&(*x).a)`. Here `*x` is a place expression of type `T`, and `(*x).a` is a place expression of type `Foo`. Then we borrow the place with `&<place>` to make a new pointer to it.

Places are also the basis onto which the borrow-checker operates; they're already what we want to reason about for aliasing and such.

The conclusion I draw: places are a powerful concept and something users are familiar with, we should make use of it.

### Proposed solution: custom places

I propose that custom smart pointers should also work via places: if `x: MyPtr<T>`, then `*x` would be a place expression of type `T`, and there would be a syntax to "borrow as that pointer": (bikeshed pending) `@MyPtr x.a.b`. A full desugaring of `x.a.b.foo()` may then be:
```rust
Foo::foo(@MyPtr (*x).a.b)
// This is borrow-checked, then compiles to a method call like:
Foo::foo(<MyPtr as Project>::project(&raw const x, projection_type!(T, a.b)))
```

I'm imagining a `HasPlace` trait: if `x: X` and `X: HasPlace`, then `*x` is allowed and is a place expression of type `X::Target`.

```rust
trait HasPlace {
    type Target: ?Sized;
}
```

In today's rust there are a few things we can do to a place:
- read from it: `let x = <place>;`
- write to it: `<place> = something();`
- borrow it: `&<place>`, `&mut`, `&raw const`, `&raw mut`;
- read its discriminant: `match <place> { Some(_) => ..., None => ..., }`.

I propose that each of these operations should be governed by a different trait. Because each is opt-in, this allows for virtual places that don't correspond to a region in memory, e.g. in the struct-of-arrays representation (see worked-out example at the end).

Moreover, each smart pointer becomes its own kind of borrow. So one could write e.g. `@RcRef (*x).a.b` where `x: Rc<T>` (using the [`rcref` crate](https://docs.rs/rcref/latest/rcref/struct.RcRef.html)). This is how we implement the "pointer projection" feature that the Field Projection initiative has been discussing.


## Detailed Proposal

```rust
/// A type that "contains" another. This is used for smart pointers, and for
/// containers/wrappers whose purpose is to "contain" or "modify" a wrapped
/// type.
///
/// If `X: HasPlace` and `x: X`, the expression `*x` is a place expression and
/// has type `X::Target`. What can be done with this place depends on the other
/// `Place*` traits in this module.
trait HasPlace {
    /// The type of the contained value.
    type Target: ?Sized;
}

/// Describes the location of a subvalue of type `Target` within a value of type
/// `Source`.
trait Projection: Copy {
    type Source: ?Sized;
    type Target: ?Sized;
    // Apply the projection to a pointer.
    unsafe fn do_project(self, *mut Self::Source) -> *mut Self::Target;
}

/// Allows naming the type of a projection. All projections are supported:
/// struct field, union field, enum variant field, indexing.
/// E.g. `projection_type((MyStruct.field[_].other_field as Some).0): Projection`
macro projection_type! { ... }
/// Alias for `projection_type` that doesn't allow fields. Represents the noop
/// projection that maps a type to itself.
macro empty_projection! { ... }

/// Tells the borrow-checker what other simultaneous and subsequent borrows
/// of the same place are allowed. E.g. if `let y = @MyMutPtr x.a;` and another
/// borrow of `x.a` is taken, `y` can no longer be used and must be dropped.
///
/// Note that the question of "which borrows can be taken from inside a given
/// place" is governed by `PlaceBorrow`; there's no borrowck-enforced limitation
/// that e.g. a `Unique` borrow can't be derived from a `Shared` one.
//
// TODO: does that all make sense without lifetimes?
// TODO: what about borrows to parent/child places? `&x.a.b` and `&x.a` can
// coexist but not `&x.a.b` and `&mut x.a`.
// TODO: how does this interact with the other actions on places (read, write)?
// This needs more work.
#[non_exhaustive]
enum BorrowKind {
    /// Other borrows are allowed (like `*mut T` and `RcRef<T>`).
    Untracked,
    /// Other `Shared` simultaneous borrows are allowed (like `&T`).
    Shared,
    /// No other simultaneous tracked borrows are allowed (like `&mut T`).
    Unique,
    /// No other simultaneous or subsequent borrows are allowed (like `&own T`).
    Owning,
    /// No other simultaneous tracked borrows are allowed and `drop` must be
    /// called before the underlying memory is reclaimed (like `&pin mut T`).
    UniquePinning,
    // maybe other things?
}

/// "Borrow" a subplace of a custom place as the chosen place container `X`.
///
/// The syntax for this is `@SmartPtr <place_expr>` (or, for the built-in
/// pointers, `&<place>`, `&mut <place>`, etc). The type of the last
/// (innermost) dereference in `place_expr` gives the `Self` type; the X type is
/// `SmartPtr<_>` where we let inference figure out the type params. We can use
/// the constraint that `X::Target == P::Target` is the type of `<place_expr>`.
/// If `<place_expr>` does not contain a dereference,
/// we pretend it is dereferencing a `LocalPlace<S>` (defined below).
///
/// Safety: `borrow` must construct a new place container that points to the 
/// place obtained by offsetting `**self` by `p.offset()`. It must not touch any
/// other bytes of the place `**self` (as they may be borrowed or uninit).
unsafe trait PlaceBorrow<'a, P, X>
where
  P: Projection,
  Self: HasPlace<P::Source>,
  X: HasPlace<P::Target>
{
    /// Whether the operation is safe.
    const SAFE: bool;

    /// Tells the borrow-checker what other simultaneous and subsequent
    /// borrows are allowed.
    const BORROW_KIND: BorrowKind;

    /// Safety: `p` must be a valid projection for the `**self` place for the
    /// duration of `'a`. This includes respecting the aliasing constraints
    /// described by `X::BORROW_KIND`.
    unsafe fn borrow(*const self, p: P) -> X;
}


// Basic stuff.
impl<P> PlaceBorrow<'a, P, &'a P::Target>
                       for &'a P::Source
  where P: Projection
{ ... }
impl<P> PlaceBorrow<'a, P, &'a mut P::Target>
                       for &'a mut P::Source
  where P: Projection
{ ... }
impl<P> PlaceBorrow<'a, P, &'a P::Target>
                       for &'a mut P::Source
  where P: Projection
{ ... }
// The basic `Deref` behavior.
impl<P> PlaceBorrow<'a, P, &'a P::Target>
                       for Arc<P::Source>
  where P: Projection
{ ... }

// Some fun things we wanted in the Field Projections initiative.
impl<P> PlaceBorrow<'_, P, ArcRef<P::Target>>
                       for Arc<P::Source>
  where P: Projection
{ ... }
// See https://hackmd.io/@rust-lang-team/S1I1aEc_lx#RCU-Read-Copy-Update
impl<P, T> PlaceBorrow<'a, P, &'a Rcu<T>>
                          for &Mutex<P::Source>
  where P: Projection<Target=Rcu<T>>
{ ... }


// Blanket impl of `Deref` whenever the pointer allows shared reborrows.
impl<T> Deref for T
where
    T: HasPlace
    T: for<'a> PlaceBorrow<'a, empty_projection!(T::Target), &'a T::Target>
{
    type Target = <T as HasPlace>::Target;
    fn deref(&self) -> &Self::Target { &**self }
}
impl<T> DerefMut for T { ... } // Same thing

/// Write to a place. Syntax is `<place_expr> = something();`.
trait PlaceWrite<P: Projection>: HasPlace<Target=P::Source> {
    /// Whether the operation is safe.
    const SAFE: bool;

    /// Safety: aliasing must be correctly enforced etc.
    unsafe fn write(*mut self, p: P, x: P::Target);
    unsafe fn write_from(*mut self, p: P, ptr: *const P::Target);
}

/// Read from a place. Syntax is e.g.`let x = <place_expr>`, or
/// `something(<place_expr>)`.
/// Note that reads can be either copies or moves, the smart pointer
/// doesn't get to observe the difference. Instead moves are allowed if `PlaceMove` is implemented, and the borrow-checker makes sure to 
trait PlaceRead<P: Projection>: HasPlace<Target=P::Source> {
    /// Whether the operation is safe.
    const SAFE: bool;

    /// Safety: aliasing must be correctly enforced etc.
    unsafe fn read(*const self, p: P) -> P::Target;
    unsafe fn read_to(*const self, p: P, ptr: *mut P::Target);
    unsafe fn read_discriminant(*const self, p: P) -> P::Target::Discriminant;
}

/// The place wrapper used when borrowing from a stack local. E.g.
/// ```rust
/// impl Foo { fn method(self: MyPtr<Self>) { ... } }
/// let x: Foo = ...;
/// x.method();
/// // desugars to
/// Foo::method(@MyPtr(x))
/// // which calls into `impl PlaceBorrow<P, MyPtr<U>> for LocalPlace<T>`.
/// ```
/// See discussion about orphan rules below.
#[repr(transparent)]
pub struct LocalPlace<T>(T);
impl<T> HasPlace<T> for LocalPlace<T> { ... }
```

### Moving values out

From a borrowck perspective, moving values out isn't hard: it's operationally a pointer read that leaves the value as partially moved-out-of. Drop elaboration can make sure to drop all the other parts of the value.

What remains then is a pointer in an invalid state, e.g. a `Box<T>` with a moved-out `T`, which still needs to get cleaned up (e.g. to dealloc the memory), or made full again. Recycling an idea from Benno, this could look like:

```rust
/// Place containers that allow moving out of the place. The actual moves are
/// done with `PlaceRead`. If `DropHusk` is not implemented, borrowck
/// will require any moved-out-of subplaces to have a new value written to them
/// before the end of the scope.
// We could make this trait take a `P: Projection` so that users can restrict
// moving to certain types.
trait PlaceMove {}

/// Place containers that allow either moves or `BorrowKind::Owned` borrows. If
/// any part of the place is considered moved out of, drop
/// elaboration will make sure to drop the rest and call `drop_husk` for the
/// final cleanup.
trait DropHusk: HasPlace {
    /// Safety: `self` points to a valid but unsafe value of `Self` obtained by
    /// reading or dropping subplaces of this one.
    unsafe fn drop_husk(*mut self);
}

fn foo(x: MyPtr<Foo>) {
    drop(x.a);
}
// becomes after drop elaboration:
fn foo(x: MyPtr<Foo>) {
    drop(x.a);
    core::mem::drop_in_place(&raw mut x.b);
    (&raw mut x).drop_husk();
}
```

If the pointer doesn't support `*mut`-reborrowing, we may do `drop(x.b)` instead (which moves the value out first).

This seamlessly supports moving some values out (using `PlaceRead`) then moving others back in (using `PlaceWrite`): borrowck ensures that we either get a full value at the end or that everything is properly dropped.

### Reborrowing

To be truly first-class, custom places should get reborrowing like `&mut` does:
```rust
fn foo(x: MyMutPtr<Foo>) {}

let x = ...;
foo(x);
foo(x);
```

Seems easy enough to use the same mechanism: wherever a `&mut` reborrow would have been inserted, to the same for custom places. E.g. here the first `foo(x)` would become `foo(@MyMutPtr *x)`.

Note this important edge about reborrowing: https://haibane-tenshi.github.io/rust-reborrowing/ . Our proposal does _not_ run into this, because the target lifetime is given to us by the borrow-checker, with no trait-system relation to the source lifetime. We let the borrow-checker enforce the correct relation, which I think makes it work correctly. Or maybe we'll need some relationship. But there for sure isn't the outer `&mut` that caused the issue mentioned in the blog post.

```rust
impl<T, P: Projection> PlaceBorrow<'a, P, MyMutPtr<'a, P::Target>> for MyMutPtr<'_, T> { ... }
```

This proposal is however in tension with the [`Reborrow` project goal](https://github.com/rust-lang/rust/issues/145612): they want to support a lot more than place-like things:

```rust
fn foo(x: Option<&mut Foo>) {}

let x = ...;
foo(x);
foo(x);
```

Their reborrowing moreover explicitly aims to be "a memory copy with added lifetime analysis", whereas we run custom code.

There's a bit of an impedance mismatch between the two ideas: the "just lifetime analysis" view is useful for the majority of types and composable. Our one is natural given everything else we've put in place, but only works for place-like things.

And if `Option<&mut Foo>` can be auto-reborrowed, then we'd want `Option<MyMutPtr<Foo>>` too! But that will need to run code, e.g. if the pointer is `Rc`-backed.

Of note (thanks Benno!) is that this interacts with the ergonomic-ref-counting proposal. On way to
cut this apple would be a `unsafe trait TrivialReborrow` that guarantees that trivial-projection
reborrowing runs no code and only allow auto-reborrows for such places. This would make it
compatible with the `Reborrow` proposals.

### Double-deref

That's a non-obvious part of the feature: what of `**x` for `P<Q<T>>`? E.g. `MyPtr<&T>`.

The first point is that we can't get the `Q<T>` by value here if it's not `Copy`. Even with compiler-emitted unsafe, that would break things if e.g. it contains a `Cell`.

So I imagine:
```rust
let x: P<Q<Foo>> = ...;
let a = @R (**x).a;
// `**x` is a `Q`-kind-of-place, so presumably we use `impl PlaceBorrow<P, R<A>>
// for Q<Foo>`. that means we need a `*const Q<Foo>`. obvious desugar then:
let _tmp: *const Q<Foo> = &raw const *x;
let a = <_ as PlaceBorrow<..>>::borrow(_tmp, proj_type!(Foo.a));
```
This makes `*const`-reborrowing quite central. If `Q<Foo>: Copy` we'd only need `PlaceRead` which is
much nicer. Could even relax `Copy` a bit since reading out a `&mut` is probably fine. I'm unsure
what's best here.

A possibly-overengineered alternative would be custom behavior with a trait. Actually idk how that would work. I'm secretly hoping I can encode the "`&&mut T` becomes `&T`" behavior of match ergonomics into a trait. Someone should stop me before I go too far x).
```rust
/// Dereference a pointer contained in the current place.
trait PlaceDeref<P, X>
where
  P: Projection,
  Self: HasPlace<Target: HasPlace>, {
      // uhhh
}
```

### Pattern-matching

Pattern-matching is based on places, and we can make it work with custom places too!

```rust
fn foo(x: MyPtr<Option<Foo>>) {
    match *x {
        Some(ref foo) => ...,
        None => ...,
    }
}
// compiles to:
fn foo(x: MyPtr<Option<Foo>>) {
    let d = (&raw const x).read_discriminant(empty_projection!(Option<Foo>));
    match d {
        Some => {
            let foo = &(*x).Some.0;
            ...
        }
        None => ...,
    }
}
```

The first question is the syntax for custom-borrowing in patterns. I could imagine using the same `@MyPtr`: `Some(@MyPtr foo)`. It's not fully satisfactory, bikeshed open.

The second question is match ergonomics. What should this do:
```rust
fn foo(x: MyPtr<Option<Foo>>) {
    match x {
        Some(foo) => ...,
        None => ...,
    }
}
```
Arguably this could do `let foo = @MyPtr x.Some.0` automatically. But then what if `x: &MyPtr<Option<Foo>>`? Or `x: MyPtr<&Option<Foo>>`? Or `x: Pin<&mut Option<Foo>>` of course? I don't know a good answer yet.

My first instinct would be "keep the innermost pointer type". For `x: &MyPtr<Option<Foo>>` that would mean using `&raw const *x: *const MyPtr<..>` to get a `MyPtr<Foo>`. Who knows it that makes sense in general... Feels related to the double-deref point above.

### Not just one-generic-param types

Something I glanced over a bit: what if I want to borrow as `MyPtr<A, B, T>`? Or `Pin<&mut T>`? Or `&mut MaybeIninit<T>`?

In principle that's not a problem, you just need the right `PlaceBorrow` impl. But the syntax for
borrowing, idk. `@<Pin<&mut _>> place`? Sad face.

### Non-indirected containers

Things we want to project include non-indirected containers like `Cell`, `MaybeUninit`, `ManuallyDrop`, `RefCell`. I haven't thought a lot about how they could fit in here.

The difficulty is that they're usually combined with smart pointers. So `MyPtr<MaybeUninit<Foo>>` can project to `MyPtr<MaybeUninit<Field>>`. I think the right way to think about this one is that `MaybeUninit<Foo>` morally has a field of type `MaybeUninit<Field>` (rather than treating `MyPtr<MaybeUninit<_>>` as a new smart pointer).

At the same time `MaybeUninit: HasPlace` seems to make perfect sense. There might be more there. The
[Field projection v2 RFC](https://github.com/rust-lang/rfcs/pull/3735) has good thoughts on the
topic; they also propose we treat these with a different concept than we treat "smart pointer"
things.

Syntactically I'm fond of `MaybeUninit` etc working as an operation from places to places: `&mut
@MaybeUninit(*x)`. Maybe it's `IsPlace` then. Big design space there.

### Projections include indexing

Place projections in rust include field projections, but also indexing (see [compiler docs](https://doc.rust-lang.org/nightly/nightly-rustc/rustc_middle/mir/type.PlaceElem.html))! In fact, did you know that the borrow-checker does know how to track constant indices? It's only observable with slice patterns:

```rust
fn foo(mut x: Box<[u32]>) {
    let [ref mut a, ..] = *x else { return };
    let [_, ref mut b, ref mut rest @ ..] = *x else { return };
    *a = 0;
    *b = 0;
}
// gives MIR like:
fn foo(mut x: Box<[u32]>) {
    if x.len() <= 1 { return }
    let a = &mut (*x)[constant 0];
    if x.len() <= 2 { return }
    let b = &mut (*x)[constant 1];
    *a = 0;
    *b = 0;
    c.write_with(|_| 0);
}
```

The borrow-checker can tell that these two borrows are disjoint, even though it refuses to do the same for user-written indexing.

For our purposes, this means that for truly first-class support, we need indexing projections. That's why `Projection::offset` is a method instead of an associated constant: the indexing projection needs to carry its index.

### Inspecting projections

Big design space, see
[discussion](https://rust-lang.zulipchat.com/#narrow/channel/522311-t-lang.2Fcustom-refs/topic/Naming.20the.20type.20for.20nested.20projections/with/553898757).
Also made much simpler if we do field-by-field.

## Questions and musings

### The question of safety

These proposed traits are all very unsafe; could it be possible to implement them safely in some cases?

Answer: I believe not. We need unsafe to express simultaneous borrows, because if another borrow is live the smart pointer is in an unsafe state.

In fact the safe version already exists in the form of `Deref`/`DerefMut`; as we saw they are fundamentally limited in the aliasing they can allow.

The safety requirements do be very WIP, more work needed there and on the interaction with borrowck.

### Ergonomics

This proposal does have a large ergonomic cost. I often impl `Deref`/`DerefMut` on wrapper structs and such; using custom place projections to get finer aliasing requires this mess of unsafe impls:
```rust
struct Data { ... }
struct WrappedData {
    data: Data,
    ...
}

impl HasPlace for WrappedData {
    type Target = Data;
}
impl<P> PlaceBorrow<'a, P, &'a P::Target> for WrappedData
  where P: Projection<Target=Source>
{
    const SAFE: bool = true;
    const BORROW_KIND: BorrowKind = BorrowKind::Shared;
    // Safety: ...
    unsafe fn borrow(*const self, p: P) -> &'a P::Target {
        // Safety: ...
        unsafe {
            <&Data as PlaceBorrow<'a, P, &_>>::borrow(&raw const self.data, p)
        }
    }
}
impl<P> PlaceBorrow<'a, P, &'a mut P::Target> for WrappedData
  where P: Projection<Target=Source>
{ /* same */ }
```
With these impls I can simultaneously borrow fields of the contained `Data`:
```rust
let a = &mut wrapped_data.data_field1;
let b = &mut wrapped_data.data_field2;
```
I still can't combine that with borrows of other fields of `WrappedData` itself however: whilst some place-derived borrows are live, the pointer becomes inaccessible except for other place-derived operations.

I think we may have to bite this bullet. We've seen that we need unsafe, and there's inherent complexity to wanting type-changing reborrows and borrowck-tracked simultaneous borrows.

If we change our mind on multi-level projections, this could help with ergonomics.

### Corner case: packed structs

Packed structs are funny because they have fields we can read/write from but not take references to. In this proposal those would be places that can be read/written to but not always borrowed. Basically only pointers that support unaligned reads should be able to borrow such fields.

The question is whether packed struct fields can work like normal projections. I think yes, and borrowck would allow PlaceRead/PlaceWrite but not PlaceBorrow on such places. Either that or we could have a `MaybeUnalignedProjection` trait.

### Adjacent problem space: partial borrows/view types

A recurring feature idea is view types and/or partial borrows, e.g. [this](https://smallcultfollowing.com/babysteps/blog/2025/02/25/view-types-redux/).

For view types, I'm thinking they could be a new kind of projection: from `MyPtr<Data>` to `MyPtr<{field1, field2} Data>`.

For partial borrows, idk. Is the partiality part of the reference? Part of the lifetime?

### Fun application: struct-of-arrays

Here's a sketch of SoA in action (assuming a particular way to inspect projections, see [here](https://rust-lang.zulipchat.com/#narrow/channel/522311-t-lang.2Fcustom-refs/topic/Naming.20the.20type.20for.20nested.20projections/with/553898757)):
```rust
struct Foo {
  a: A,
  b: B,
}

struct SoAFoo {
    a: Vec<A>,
    b: Vec<B>,
}
impl HasPlace for SoAFoo {
    type Target = [Foo];
}
impl SoAFoo {
    pub fn idx(&self, idx: usize) -> SoAElem<'_> {
        assert!(idx < self.a.len());
        SoAElem { soa: self, idx }
    }
}

struct SoAElem<'a> {
    soa: &'a SoAFoo,
    idx: usize,
}
impl HasPlace for SoAElem<'_> {
    type Target = Foo;
}

// Read the whole of `Foo`
impl<P: Projection> PlaceRead<ProjectBase<Foo>> for SoAElem<'_> {
    unsafe fn read(*const Self, p: ProjectBase<Foo>) -> Foo {
        Foo { a: self.soa.a[self.idx], b: self.soa.a[self.idx] }
    }
}
// Also `PlaceWrite` for the empty projection, but not `PlaceBorrow`.

// Get a pointer into a subplace of `Foo.a`.
impl<P: Projection> PlaceBorrow<'_, ProjectChain<frt!(Foo, a), P>, *mut P::Target> for SoAElem<'_> {
    unsafe fn borrow(*const Self, p: ProjectChain<frt!(Foo, a), P>) -> *mut P::Target {
        let ProjectChain(_proj_a, proj_tail) = p;
        <&A as PlaceRead<P>>::borrow(&raw const self.soa.a[self.idx], proj_tail)
    }
}
// Same for `Foo.b`.
impl<P: Projection> PlaceBorrow<'_, ProjectChain<frt!(Foo, b), P>, *mut P::Target> for SoAElem<'_> {
    // same thing
}
// Using these two, we can implement other borrows as well as reading and writing.

// Any projection on `SoAElem` is allowed on `SoAFoo` after an indexing
// projection.
// Note: `indexing_proj!(Foo): ProjElem<Source=[Foo], Target=Foo>.
impl<'a, P, X> PlaceBorrow<'a, ProjectChain<indexing_proj!(Foo), P>, X> for SoAFoo
where
    P: Projection,
    SoAElem<'_>: PlaceBorrow<'a, P, X>
{
    unsafe fn borrow(*const Self, p: ProjectChain<...>) -> X {
        let ProjectChain(idx_proj, proj_tail) = p;
        let idx = idx_proj.offset / size_of!(Foo);
        <_ as PlaceBorrow>::borrow(&raw const self.get(idx), proj_tail)
    }
}
// Same forwarding for `PlaceRead` and `PlaceWrite`
// TODO: what about range indexing? 

#[test]
fn test() {
    let soa: SoAFoo = ...;
    let elem: SoAElem = soa.get(0);
    
    // There's no Foo in memoty to borrow so we can't, however we can read and write.
    let foo: &Foo = &*elem; // ERROR
    let foo: Foo = *elem; // ok
    *elem = Foo::new(); // ok
    
    // And we can do anything with the fields.
    let a = &elem.a; // works
    let aa = &elem.a.blah; // works
}
```

### Fun application: `&own` references

This proposal allows ergonomic owning references (described e.g.
[here](https://internals.rust-lang.org/t/a-sketch-for-move-semantics/18632/19?u=illicitonion)): they
take ownership of the value and are responsible for dropping it, but don't own the allocation
itself.

```rust
struct Own<'a, T>(*const T, PhantomData<&'a T>);
impl HasPlace for Own<'_, T> {
  ..
}

// Borrowck gives us this `'a` and will use it to know how long the borrow lasts.
impl PlaceBorrow<'a, P, Own<'a, P::Target>> for Box<P::Source> {
    // This means that any reborrow into `Own` is treated like a move out by borrowck,
    // in the sense that no other access to that place is allowed until another value
    // is written to it.
    const BORROW_KIND: BorrowKind = BorrowKind::Owned;
    ..
}
impl PlaceRead<..> for Own<..> { .. }
impl PlaceMove<..> for Own<..> { .. }

// Now we can write:
let b: Box<Foo> = ...;
let own_a: Own<'_, A> = @Own b.a;
b.a = another_a(); // error: the place is borrowed
let a = *own_a; // reads the value, then calls `PlaceMove::drop_husk` on `own_a`
let _ = &b.a; // error: the place is moved out
use(b); // error: b is partially moved out of
b.a = another_a(); // ok
// Here `b` is normal again.
let own_a: Own<'_, A> = @Own b.a;
// `b.a` is considered moved out here, so at the end of the function `b.other_field` will be
// dropped if there is one, and `PlaceMove::drop_husk` will be called on `b`
```
