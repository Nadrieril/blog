---
title: "First-Class Smart Pointers, cont."
date: 2025-12-17 23:25 +0100
---

- Containers https://rust-lang.zulipchat.com/#narrow/channel/522311-t-lang.2Fcustom-refs/topic/Non-indirected.20containers.20with.20virtual.20fields/with/563576728

One of the big questions
PhantomData! hihi

How does containers compose with SoA? Not well, idk

```
/// Transforms any projection available on `Self::Target` into a projection on `Self`. This works
/// like a virtual field with chosen target type, e.g. if `x: MaybeUninit<(A, B)>`, `x.1` is a valid
/// place of type `MaybeUninit<A>`.
trait PlaceWrap<P: Projection<Source = Self::Target>>: HasPlace {
    type Proj: Projection<Source = Self, Target: HasPlace>;
    fn wrap_proj(p: &P) -> Self::Proj;
}
```

- Enums https://rust-lang.zulipchat.com/#narrow/channel/522311-t-lang.2Fcustom-refs/topic/Projecting.20through.20enums/with/563576190

For interior mutable wrappers, `InfallibleProjection`.

- Canonical projections, `@const`/`@mut`, how to nest derefs?
- Match ergonomics: canonical project?
- Inspecting projections
- Closure capture
- Interactions with other features
    - arbitrary_self_types (particularly the design of Receiver);
    - pin ergonomics;
    - reborrowing;
    - ergonomic Rc;
    - possibly in-place init;
    - match ergonomics 2.0;
    - postfix macros (realized that this morning).
