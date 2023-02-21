from ufo2ft.featureWriters import MarkFeatureWriter, BaseFeatureWriter, ast
from types import SimpleNamespace
from fontTools.feaLib.variableScalar import VariableScalar
from collections import OrderedDict, defaultdict


class VariableMarkWriter(MarkFeatureWriter):
    def setContext(self, *args, **kwargs):
        # Rename "font" to "designspace" to avoid confusion
        super(MarkFeatureWriter, self).setContext(*args, **kwargs)
        self.context = SimpleNamespace(
            designspace=self.context.font,
            feaFile=self.context.feaFile,
            compiler=self.context.compiler,
            todo=self.context.todo,
            insertComments=self.context.insertComments,
        )
        self.context.font = self.context.designspace.findDefault().font
        self.context.axis_map = {
            axis.name: axis.tag for axis in self.context.designspace.axes
        }
        self.context.axis_to_userspace = {
            axis.name: axis.map_backward for axis in self.context.designspace.axes
        }
        self.context.gdefClasses = self.getGDEFGlyphClasses()
        self.context.anchorLists = self._getAnchorLists()
        self.context.anchorPairs = self._getAnchorPairs()
        self.context.feaScripts = set(ast.getScriptLanguageSystems(self.context.feaFile).keys())
        return self.context

    def get_location(self, location):
        return {
            self.context.axis_map[name]: self.context.axis_to_userspace[name](v)
            for name, v in location.items()
        }

    def _maybeNonVariable(self, varscalar):
        values = list(varscalar.values.values())
        if not any(v != values[0] for v in values[1:]):
            return list(varscalar.values.values())[0]
        return varscalar

    def _getAnchor(self, glyphName, anchorName):
        x_value = VariableScalar()
        y_value = VariableScalar()
        for source in self.context.designspace.sources:
            glyph = source.font[glyphName]
            for anchor in glyph.anchors:
                if anchor.name == anchorName:
                    location = self.get_location(source.location)
                    x_value.add_value(location, anchor.x)
                    y_value.add_value(location, anchor.y)
        return self._maybeNonVariable(x_value), self._maybeNonVariable(y_value)

    def _getAnchorLists(self):
        gdefClasses = self.context.gdefClasses
        if gdefClasses.base is not None:
            # only include the glyphs listed in the GDEF.GlyphClassDef groups
            include = gdefClasses.base | gdefClasses.ligature | gdefClasses.mark
        else:
            # no GDEF table defined in feature file, include all glyphs
            include = None
        result = OrderedDict()
        for glyphName, glyph in self.getOrderedGlyphSet().items():
            if include is not None and glyphName not in include:
                continue
            anchorDict = OrderedDict()
            for anchor in glyph.anchors:
                anchorName = anchor.name
                if not anchorName:
                    self.log.warning(
                        "unnamed anchor discarded in glyph '%s'", glyphName
                    )
                    continue
                if anchorName in anchorDict:
                    self.log.warning(
                        "duplicate anchor '%s' in glyph '%s'", anchorName, glyphName
                    )
                x, y = self._getAnchor(glyphName, anchorName)
                a = self.NamedAnchor(name=anchorName, x=x, y=y)
                anchorDict[anchorName] = a
            if anchorDict:
                result[glyphName] = list(anchorDict.values())
        return result

    # Following methods only here because we want to use our own classes which don't
    # do OT Rounding

    def _defineMarkClass(self, glyphName, x, y, className, markClasses):
        anchor = ast.Anchor(x=x, y=y)
        markClass = markClasses.get(className)
        if markClass is None:
            markClass = ast.MarkClass(className)
            markClasses[className] = markClass
        else:
            if glyphName in markClass.glyphs:
                mcdef = markClass.glyphs[glyphName]
                if self._anchorsAreEqual(anchor, mcdef.anchor):
                    self.log.debug(
                        "Glyph %s already defined in markClass @%s",
                        glyphName,
                        className,
                    )
                    return None
                else:
                    # same mark glyph defined with different anchors for the
                    # same markClass; make a new unique markClass definition
                    newClassName = ast.makeFeaClassName(className, markClasses)
                    markClass = ast.MarkClass(newClassName)
                    markClasses[newClassName] = markClass
        glyphName = ast.GlyphName(glyphName)
        mcdef = ast.MarkClassDefinition(markClass, anchor, glyphName)
        markClass.addDefinition(mcdef)
        return mcdef

    def _makeMarkToBaseAttachments(self):
        markGlyphNames = self.context.markGlyphNames
        baseClass = self.context.gdefClasses.base
        result = []
        for glyphName, anchors in self.context.anchorLists.items():
            # exclude mark glyphs, or glyphs not listed in GDEF Base
            if glyphName in markGlyphNames or (
                baseClass is not None and glyphName not in baseClass
            ):
                continue
            baseMarks = []
            for anchor in anchors:
                if anchor.markClass is None or anchor.number is not None:
                    # skip anchors for which no mark class is defined; also
                    # skip '_1', '_2', etc. suffixed anchors for this lookup
                    # type; these will be are added in the mark2liga lookup
                    continue
                assert not anchor.isMark
                baseMarks.append(anchor)
            if not baseMarks:
                continue
            result.append(MarkToBasePos(glyphName, baseMarks))
        return result

    def _makeMarkToMarkAttachments(self):
        markGlyphNames = self.context.markGlyphNames
        # we make a dict of lists containing mkmk pos rules keyed by
        # anchor name, so we can create one mkmk lookup per markClass
        # each with different mark filtering sets.
        results = {}
        for glyphName, anchors in self.context.anchorLists.items():
            if glyphName not in markGlyphNames:
                continue
            for anchor in anchors:
                # skip anchors for which no mark class is defined
                if anchor.markClass is None or anchor.isMark:
                    continue
                if anchor.number is not None:
                    self.log.warning(
                        "invalid ligature anchor '%s' in mark glyph '%s'; " "skipped",
                        anchor.name,
                        glyphName,
                    )
                    continue
                pos = MarkToMarkPos(glyphName, [anchor])
                results.setdefault(anchor.key, []).append(pos)
        return results

    def _makeMarkToLigaAttachments(self):
        markGlyphNames = self.context.markGlyphNames
        ligatureClass = self.context.gdefClasses.ligature
        result = []
        for glyphName, anchors in self.context.anchorLists.items():
            # exclude mark glyphs, or glyphs not listed in GDEF Ligature
            if glyphName in markGlyphNames or (
                ligatureClass is not None and glyphName not in ligatureClass
            ):
                continue
            componentAnchors = {}
            for anchor in anchors:
                if anchor.markClass is None and anchor.key:
                    # skip anchors for which no mark class is defined
                    continue
                assert not anchor.isMark
                number = anchor.number
                if number is None:
                    # we handled these in the mark2base lookup
                    continue
                # unnamed anchors with only a number suffix "_1", "_2", etc.
                # are understood as the ligature component having <anchor NULL>
                if not anchor.key:
                    componentAnchors[number] = []
                else:
                    componentAnchors.setdefault(number, []).append(anchor)
            if not componentAnchors:
                continue
            ligatureMarks = []
            # ligature components are indexed from 1; any missing intermediate
            # anchor number means the component has <anchor NULL>
            for number in range(1, max(componentAnchors.keys()) + 1):
                ligatureMarks.append(componentAnchors.get(number, []))
            result.append(MarkToLigaPos(glyphName, ligatureMarks))
        return result


def otRound(foo):
    return foo


class AbstractMarkPos:
    """Object containing all the mark attachments for glyph 'name'.
    The 'marks' is a list of NamedAnchor objects.
    Provides methods to filter marks given some callable, and convert
    itself to feaLib AST 'pos' statements for mark2base, mark2liga and
    mark2mark lookups.
    """

    Statement = None

    def __init__(self, name, marks):
        self.name = name
        self.marks = marks

    def _filterMarks(self, include):
        return [anchor for anchor in self.marks if include(anchor)]

    def _marksAsAST(self):
        return [
            (ast.Anchor(x=otRound(anchor.x), y=otRound(anchor.y)), anchor.markClass)
            for anchor in sorted(self.marks, key=lambda a: a.name)
        ]

    def asAST(self):
        marks = self._marksAsAST()
        return self.Statement(ast.GlyphName(self.name), marks)

    def __str__(self):
        return self.asAST().asFea()  # pragma: no cover

    def filter(self, include):
        marks = self._filterMarks(include)
        return self.__class__(self.name, marks) if any(marks) else None

    def getMarkGlyphToMarkClasses(self):
        """Return a list of pairs (markGlyph, markClasses)."""
        markGlyphToMarkClasses = defaultdict(set)
        for namedAnchor in self.marks:
            for markGlyph in namedAnchor.markClass.glyphs:
                markGlyphToMarkClasses[markGlyph].add(namedAnchor.markClass.name)
        return markGlyphToMarkClasses.items()


class MarkToBasePos(AbstractMarkPos):

    Statement = ast.MarkBasePosStatement


class MarkToMarkPos(AbstractMarkPos):

    Statement = ast.MarkMarkPosStatement


class MarkToLigaPos(AbstractMarkPos):

    Statement = ast.MarkLigPosStatement

    def _filterMarks(self, include):
        return [
            [anchor for anchor in component if include(anchor)]
            for component in self.marks
        ]

    def _marksAsAST(self):
        return [
            [
                (ast.Anchor(x=otRound(anchor.x), y=otRound(anchor.y)), anchor.markClass)
                for anchor in sorted(component, key=lambda a: a.name)
            ]
            for component in self.marks
        ]

    def getMarkGlyphToMarkClasses(self):
        """Return a list of pairs (markGlyph, markClasses)."""
        markGlyphToMarkClasses = defaultdict(set)
        for component in self.marks:
            for namedAnchor in component:
                for markGlyph in namedAnchor.markClass.glyphs:
                    markGlyphToMarkClasses[markGlyph].add(namedAnchor.markClass.name)
        return markGlyphToMarkClasses.items()
