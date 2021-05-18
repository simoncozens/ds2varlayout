from ufo2ft.featureCompiler import parseLayoutFeatures
from ufo2ft.featureWriters import (
    GdefFeatureWriter,
    MarkFeatureWriter,
    ast,
    loadFeatureWriters,
    isValidFeatureWriter,
)
from fontTools.designspaceLib import DesignSpaceDocument
import sys
import importlib
import ufoLib2
from collections import OrderedDict
from types import SimpleNamespace
from ufo2ft.util import makeOfficialGlyphOrder
from fontTools.ttLib import TTFont
from ufo2ft.constants import FEATURE_WRITERS_KEY

from featureWriters.VariableKernWriter import VariableKernWriter
from featureWriters.VariableMarkWriter import VariableMarkWriter

defaultFeatureWriters = [VariableKernWriter, VariableMarkWriter]

ds = DesignSpaceDocument.fromfile(sys.argv[1])
ds.loadSourceFonts(opener=ufoLib2.Font)
defaultufo = ds.findDefault().font
featurefile = parseLayoutFeatures(defaultufo)

glyphOrder = makeOfficialGlyphOrder(defaultufo)
glyphSet = OrderedDict((gn, defaultufo[gn]) for gn in glyphOrder)
ttFont = TTFont()
ttFont.setGlyphOrder(glyphOrder)
fakecompiler = SimpleNamespace(glyphSet=glyphSet, ttFont=ttFont, axes=ds.axes)

writers = []

for wdict in defaultufo.lib[FEATURE_WRITERS_KEY]:
    moduleName = wdict.get("module", __name__)
    className = wdict["class"]
    if className == "KernFeatureWriter":
        className = "VariableKernWriter"
    elif className == "MarkFeatureWriter":
        className = "VariableMarkWriter"
    options = wdict.get("options", {})
    if not isinstance(options, dict):
        raise TypeError(type(options))
    module = importlib.import_module(moduleName)
    klass = getattr(module, className)
    if not isValidFeatureWriter(klass):
        raise TypeError(klass)
    writer = klass(**options)
    writers.append(writer)

if not writers:
    writers = [writer() for writer in defaultFeatureWriters]

for writer in writers:
    writer.write(ds, featurefile, compiler=fakecompiler)

print(featurefile)
