// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import "../src/Counter.sol";

contract CounterTest {
    Counter counter;

    function setUp() public {
        counter = new Counter();
    }

    function testIncrement() public {
        counter.increment();
        require(counter.number() == 1, "expected 1");
    }

    function testSetNumber() public {
        counter.setNumber(42);
        require(counter.number() == 42, "expected 42");
    }
}
