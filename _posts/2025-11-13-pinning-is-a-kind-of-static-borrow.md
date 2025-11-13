---
title: "Pinning is a kind of static borrow"
date: 2025-11-13 00:20 +0100
---

> Disclaimer: I am by no means a `Pin` expert, this is potentially incorrect in subtle ways, please
let me know if so!

Pin is notoriously subtle: once pinned, a place has restricted access _forever_, even after the
`Pin<&mut T>` reference goes out of scope. The reason is simple: we want to allow pointers to the
place to be stored in locations we know nothing about, so the data there better stay consistent.

In other words, a place has restricted access because some pointers to it may exist. Well this is
typically the kind of thing the borrow-checker tracks!

I propose the following idea, for the sole purpose of making `Pin` easier to understand: weak
references. `&weak T` would be a new reference kind, with the following properties:
- it doesn't allow any safe accesses;
- while it exists, no contained `!Unpin` type is allowed to be moved out of the place; other mutations are fine;
- while it exists the pointed-to place may be deallocated, on the condition that the value is dropped first.

So it's basically a raw pointer, except that while I hold a `&weak T` to a place, I know the place
will maintain _some_ consistency. In particular it won't be deallocated without calling `Drop`
first, so the `Drop` impl has the opportunity to let me know that my `&weak` reference can't be used
anymore (this is illustrated in the example below).

The way `Pin` fits into this is that we can define it as:
```rust
struct Pin<P: Deref>(P, &'static weak P::Target);
```

Here the weirdness of `Pin` is made clear: it makes the pointed-to place weakly-borrowed forever.
The API surface and safety requirements of `Pin` are all there to make sure only use the `P` pointer
in ways that don't break the invariants imposed by the `&'static weak` reference.

Note that it's ok to take a `'static` reference of a local variable here because `&weak` allows the
pointed-to place to be deallocated. Of course it can then only be used if you have some mechanism to
know whether the target is still allocated. This is definitely a weird kind of reference.

Here's how it would work in an example: intrusive linked lists (based on [Ralf's post on the
topic](https://www.ralfj.de/blog/2018/04/10/safe-intrusive-collections-with-pinning.html)).
```rust
struct Collection<T> {
    // The `Drop` impl of `Entry` guarantees that the entries listed can be accessed.
    objects: RefCell<Vec<&'static weak Entry<T>>>,
}
impl<T> !Unpin for Collection<T> {}

struct Entry<T> {
    x: T,
    // Set to `Some` if we are part of some collection.
    // The `Drop` impl of `Collection` guarantees that the collection can be accessed.
    collection: Cell<Option<&'static weak Collection<T>>>,
}
impl<T> !Unpin for Entry<T> {}

impl<T> Collection<T> {
    fn new() -> Self {
        Collection { objects: RefCell::new(Vec::new()) }
    }

    // Add the entry to the collection.
    fn insert(mut self: Pin<&mut Self>, entry: Pin<&mut Entry<T>>) {
        if entry.collection.get().is_some() {
            panic!("Can't insert the same object into multiple collections");
        }
        // Pointer from collection to entry. This `&mut` is unsafe: not all mutations
        // through it are allowed.
        let mut_this : &mut Self = unsafe { Pin::get_mut(&mut self) };
        mut_this.objects.borrow_mut().push(Pin::get_weak(&entry));
        // Pointer from entry to collection.
        let weak_this: &weak Self = Pin::get_weak(&self);
        entry.collection.set(Some(weak_this));
    }

    // Show all entries of the collection.
    fn print_all(self: Pin<&mut Self>)
    where T: Debug
    {
        print!("[");
        for entry in self.objects.borrow().iter() {
            // Safety: the `&weak` ref guarantees:
            // 1. that `entry.collection` cannot be changed and keeps pointing to this
            //   collection;
            // 2. that the entry won't be deallocated without running `Drop`.
            // The `Drop` impl of `Entry` will remove itself from its `entry.collection`,
            // so combined with the guarantees above we know that the weak refs we hold
            // here can be used.
            let entry : &Entry<T> = unsafe { &**entry };
            print!(" {:?},", entry.x);
        }
        println!(" ]");
    }
}

impl<T> Drop for Collection<T> {
    fn drop(&mut self) {
        // Go through the entries to remove pointers to the collection.
        for entry in self.objects.borrow().iter() {
            // Safety: the `&weak` ref guarantees:
            // 1. that `entry.collection` cannot be changed and keeps pointing to this
            //   collection;
            // 2. that the entry won't be deallocated without running `Drop`.
            // The `Drop` impl of `Entry` will remove itself from its `entry.collection`,
            // so combined with the guarantees above we know that the weak refs we hold
            // here can be used.
            let entry : &Entry<T> = unsafe { &**entry };
            entry.collection.set(None);
        }
    }
}

impl<T> Entry<T> {
    fn new(x: T) -> Self {
        Entry { x, collection: Cell::new(None) }
    }
}

impl<T> Drop for Entry<T> {
    fn drop(&mut self) {
        // Go through collection to remove this entry.
        if let Some(collection) = self.collection.get() {
            // Safety: the `&weak` ref guarantees:
            // 1. that `collection.objects` cannot be changed without cooperation from
            //   the `Collection` API;
            // 2. that the collection won't be deallocated without running `Drop`.
            // The `Drop` impl of `Collection` will remove itself from all the entries
            // it contains, so combined with the guarantees above we know that the
            // weak ref we hold here can be used.
            let collection : &Collection<T> = unsafe { &*collection };
            collection.objects.borrow_mut().retain(|ptr| ptr.addr() != self.addr());
        }
    }
}
```

Here the collection keeps a `&weak` reference to each entry, and we rely on the `Drop` guarantee of
`&weak` for the safety of our API. We could imagine using real lifetimes instead of `'static`: the
entry only needs to stay pinned as long as it exists inside the collection. Once we remove an entry,
its `&weak` reference goes out of scope, so we could in theory go back to doing whatever we want
with the entry.

Of note, which wasn't obvious to me, is that the collection too needs to be pinned. That's because
the entries need to keep a pointer to it. This highlights the two ingredients for a safe `Pin`-based
API:
- a lifetimeless pointer to a place;
- a mechanism for the `Drop` impl of the pinned type to make sure we don't use the pointer any
  longer.

This typically means a reference cycle, but that's not necessary. The following example doesn't
involve a reference cycle but requires pinning:
```rust
struct A<'a> {
    // While this is `true`, `ptr` is valid for accesses.
    is_ptr_valid: &'a AtomicBool,
    ptr: &'static weak B<'a>,
}
struct B<'a> {
    flag: &'a AtomicBool,
    some_data: Data,
}
impl !Unpin for B<'_> {}

impl Drop for B<'_> {
    fn drop(&mut self) {
        self.flag.store(false, Ordering::Relaxed);
    }
}
```

Note also that the reason for the linked list types being `!Unpin` is that swapping out two `Entry`s
would create an inconsistency in the reference cycle. In some way the "cause" of `Entry: !Unpin` is
the contained `&weak Collection`; so when `entry.collection.is_none()` we could in theory safely
move the entry around. Similarly the reason that `B` is `!Unpin` is to ensure the `&AtomicBool`
doesn't get swapped out while `B` is weakly borrowed.

So here we go, I personally found this enlightening: `Pin<P>` is basically a way to manage a particular
kind of untracked static borrow in a safe API. When I receive a `Pin<P>` I can store the attached
weak reference wherever I want, and holding such a weak reference limits what can happen to the
place just enough that we can keep things consistent and safe. I can't help but be impressed at how
neatly this works, kudos to those who came up with this.

Let me know if you found this mental model helpful!
