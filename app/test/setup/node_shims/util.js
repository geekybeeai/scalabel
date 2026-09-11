// Jest 26 cannot resolve the `node:` scheme; map node:util here.
module.exports = require("util")
