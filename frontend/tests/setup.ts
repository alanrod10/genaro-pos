import "@testing-library/jest-dom";
import "fake-indexeddb/auto";
import { beforeEach } from "vitest";
import { resetDBInstance } from "../src/services/db";

beforeEach(() => {
  resetDBInstance();
});
