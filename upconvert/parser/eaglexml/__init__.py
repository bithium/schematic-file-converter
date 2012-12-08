#!/usr/bin/env python2
""" The Eagle XML Format Parser """

# upconvert.py - A universal hardware design file format converter using
# Format:       upverter.com/resources/open-json-format/
# Development:  github.com/upverter/schematic-file-converter
#
# Copyright 2011 Upverter, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# Eagle components are stored in libraries, which contain devicesets,
# symbols, and packages. Symbols are logical schematic representations
# while packages are the physical representations. A deviceset
# contains gates and devices. A deviceset represents a family of
# different physical devices, all of which implement the same set of
# logical devices. The set of gates is the set of logical devices and
# each gate references the symbol which represents it. Each device
# references a single package and contains a set of connects, the
# relations between the logical pins on the gates and the physical
# pins on the device.
#
# Eagle components are mapped to OpenJSON components as follows:
#
#   + Each eaglexml device in a deviceset becomes a single openjson
#     component named LIBNAME:DEVICESETNAME:DEVICENAME which represents
#     the logical gates and, eventually, the physical package for that
#     device. The pin numbers of the logical Symbol come from the pad
#     names on the physical eaglexml device.
#
#   + There is one openjson Symbol in the logical component.
#
#   + The openjson Symbols for logical components have one openjson
#     SBody for each eaglemxl gate, in the order listed in the
#     eaglexml file.
#
# TODO: handle physical component representation

from collections import defaultdict
from math import atan2, sin, cos, pi, sqrt, radians

from upconvert.core.design import Design
from upconvert.core.annotation import Annotation
from upconvert.core.components import Component, Symbol, SBody, Pin
from upconvert.core.component_instance import ComponentInstance, SymbolAttribute
from upconvert.core.net import Net, NetPoint, ConnectedComponent
from upconvert.core.shape import Arc, Circle, Label, Line, Rectangle, Polygon

from upconvert.parser.eaglexml.generated_g import parse

EAGLE_SCALE = 10.0/9.0


class EagleXML(object):
    """ The Eagle XML Format Parser.

    This parser uses code generated by generateDS.py which converts an xsd
    file to a set of python objects with parse and export functions.
    That code is in generated.py. It was created by the following steps:

      1. Started with eagle.dtd from Eagle 6.2.0.
      2. Removed inline comments in dtd (was breaking conversion to xsd).
         The dtd is also stored in this directory.
      3. Converted to eagle.xsd using dtd2xsd.pl from w3c.
         The xsd is also stored in this directory.
      4. Run a modified version of generateDS.py with the following arguments:
           --silence --external-encoding=utf-8 -o generated.py
     """

    SCALE = 2.0
    MULT =  90 / 25.4 # mm to 90 dpi

    def __init__(self):
        self.design = Design()

        # map (component, gate name) to body indices
        self.cptgate2body_index = {}

        # map (component, gate name) to pin maps, dicts from strings
        # (pin names) to Pins. These are used during pinref processing
        # in segments.
        self.cptgate2pin_map = defaultdict(dict)

        # map (component, gate names) to annotation maps, dicts from
        # strings (name|value) to Annotations. These represent the
        # >NAME and >VALUE texts on eagle components, which must be
        # converted into component instance annotations since their
        # contents depend on the component instance name and value.
        self.cptgate2ann_map = defaultdict(dict)

        # map part names to component instances. These are used during
        # pinref processing in segments.
        self.part2inst = {}

        # map part names to gate names to symbol attributes. These
        # are used during pinref processing in segments.
        self.part2gate2symattr = defaultdict(dict)


    @staticmethod
    def auto_detect(filename):
        """ Return our confidence that the given file is an
        eagle xml schematic """

        with open(filename, 'r') as f:
            data = f.read(4096)
        confidence = 0.0
        if 'eagle.dtd' in data:
            confidence += 0.9
        return confidence


    def parse(self, filename):
        """ Parse an Eagle XML file into a design """

        root = parse(filename)

        self.make_components(root)
        self.make_component_instances(root)
        self.make_nets(root)
        self.design.scale(EAGLE_SCALE)

        return self.design


    def make_components(self, root):
        """ Construct openjson components from an eagle model. """

        for lib in get_subattr(root, 'drawing.schematic.libraries.library', ()):
            for deviceset in get_subattr(lib, 'devicesets.deviceset', ()):
                for cpt in self.make_deviceset_components(lib, deviceset):
                    self.design.components.add_component(cpt.name, cpt)


    def make_deviceset_components(self, lib, deviceset):
        """ Construct openjson components for each device in an
        eaglexml deviceset in a library."""

        for device in deviceset.devices.device:
            yield self.make_device_component(lib, deviceset, device)


    def make_device_component(self, lib, deviceset, device):
        """ Construct an openjson component for a device in a deviceset. """

        cpt = Component(lib.name + ':' + deviceset.name + ':' + device.name)

        cpt.add_attribute('eaglexml_library', lib.name)
        cpt.add_attribute('eaglexml_deviceset', deviceset.name)
        cpt.add_attribute('eaglexml_device', device.name)

        symbol = Symbol()
        cpt.add_symbol(symbol)

        assignment = PinNumberAssignment(device)

        for i, gate in enumerate(get_subattr(deviceset, 'gates.gate')):
            body, pin_map, ann_map = self.make_body_from_symbol(
                lib, gate.symbol, assignment.get_pin_number_lookup(gate.name))
            symbol.add_body(body)
            cpt.add_attribute('eaglexml_symbol_%d' % i, gate.symbol)
            cpt.add_attribute('eaglexml_gate_%d' % i, gate.name)
            self.cptgate2body_index[cpt, gate.name] = len(symbol.bodies) - 1
            self.cptgate2pin_map[cpt, gate.name] = pin_map
            self.cptgate2ann_map[cpt, gate.name] = ann_map

        return cpt


    def make_body_from_symbol(self, lib, symbol_name, pin_number_lookup):
        """ Construct an openjson SBody from an eagle symbol in a library. """

        body = SBody()

        symbol = [s for s in get_subattr(lib, 'symbols.symbol')
                  if s.name == symbol_name][0]

        for wire in symbol.wire:
            body.add_shape(self.make_shape_for_wire(wire))

        for rect in symbol.rectangle:
            rotation = make_angle('0' if rect.rot is None else rect.rot)
            x1, y1 = rotate_point((self.make_length(rect.x1),
                                   self.make_length(rect.y1)), rotation)
            x2, y2 = rotate_point((self.make_length(rect.x2),
                                   self.make_length(rect.y2)), rotation)
            ux, uy = min(x1, x2), max(y1, y2)
            lx, ly = max(x1, x2), min(y1, y2)
            body.add_shape(Rectangle(ux, uy, lx - ux, uy - ly))

        for poly in symbol.polygon:
            map(body.add_shape, self.make_shapes_for_poly(poly))

        for circ in symbol.circle:
            body.add_shape(self.make_shape_for_circle(circ))

        pin_map = {}

        for pin in symbol.pin:
            connect_point = (self.make_length(pin.x), self.make_length(pin.y))
            null_point = self.get_pin_null_point(connect_point,
                                                 pin.length, pin.rot)
            label = self.get_pin_label(pin, null_point)
            pin_map[pin.name] = Pin(pin_number_lookup(pin.name), null_point,
                                    connect_point, label)
            if pin.direction:
                pin_map[pin.name].add_attribute('eaglexml_direction', pin.direction)
            if pin.visible:
                pin_map[pin.name].add_attribute('eaglexml_visible', pin.visible)
            body.add_pin(pin_map[pin.name])

        ann_map = {}

        for text in symbol.text:
            x = self.make_length(text.x)
            y = self.make_length(text.y)
            content = '' if text.valueOf_ is None else text.valueOf_
            rotation = make_angle('0' if text.rot is None else text.rot)
            align = 'right' if is_mirrored(text.rot) else 'left'
            if rotation == 0.5:
                rotation = 1.5
            if content.lower() == '>name':
                ann_map['name'] = Annotation(content, x, y, rotation, 'true')
            elif content.lower() == '>value':
                ann_map['value'] = Annotation(content, x, y, rotation, 'true')
            else:
                body.add_shape(Label(x, y, content, align=align,
                                     rotation=rotation))

        return body, pin_map, ann_map


    def make_shape_for_wire(self, wire):
        """ Generate an openjson shape for an eaglexml wire. """

        if wire.curve is None:
            return Line((self.make_length(wire.x1),
                         self.make_length(wire.y1)),
                        (self.make_length(wire.x2),
                         self.make_length(wire.y2)))

        curve, x1, y1, x2, y2 = map(float, (wire.curve, wire.x1, wire.y1, wire.x2, wire.y2))

        if curve < 0:
            curve = -curve
            negative = True
            mult = -1.0
        else:
            negative = False
            mult = 1.0

        if curve > 180.0:
            major_arc = True
            curve = 360.0 - curve
            mult *= -1.0
        else:
            major_arc = False

        chordlen = sqrt(pow(x2 - x1, 2) + pow(y2 - y1, 2))

        radius = chordlen / (2.0 * sin(radians(curve) / 2))

        mx, my = (x1 + x2) / 2, (y1 + y2) / 2 # midpoint between arc points

        h = sqrt(pow(radius, 2) - pow(chordlen / 2, 2)) # height of isoceles

        # calculate center point
        cx = mx + mult * h * (y1 - y2) / chordlen
        cy = my + mult * h * (x2 - x1) / chordlen

        if negative:
            start_angle = atan2(y2 - cy, x2 - cx)
            end_angle = start_angle + radians(curve) - (pi if major_arc else 0.0)
        else:
            start_angle = atan2(y1 - cy, x1 - cx)
            end_angle = start_angle + radians(curve) + (pi if major_arc else 0.0)

        return Arc(self.make_length(cx), self.make_length(cy),
                   round(start_angle / pi, 3) % 2.0,
                   round(end_angle / pi, 3) % 2.0,
                   self.make_length(radius))


    def make_shapes_for_poly(self, poly):
        """ Generate openjson shapes for an eaglexml polygon. """

        # TODO: handle curves
        opoly = Polygon()
        for vertex in poly.vertex:
            opoly.add_point(self.make_length(vertex.x),
                            self.make_length(vertex.y))
        yield opoly


    def make_shape_for_circle(self, circ):
        """ Generate an openjson shape for an eaglexml circle. """

        ocirc = Circle(self.make_length(circ.x),
                       self.make_length(circ.y),
                       self.make_length(circ.radius))

        ocirc.add_attribute('eaglexml_width', circ.width)

        return ocirc


    def get_pin_null_point(self, (x, y), length, rotation):
        """ Return the null point of a pin given its connect point,
        length, and rotation. """

        if length == 'long':
            distance = int(27 * self.SCALE) # .3 inches
        elif length == 'middle':
            distance = int(18 * self.SCALE) # .2 inches
        elif length == 'short':
            distance = int(9 * self.SCALE) # .1 inches
        else: # point
            distance = 0

        if rotation is None:
            rotation = ""

        if rotation.endswith('R90'):
            coords = (x, y + distance)
        elif rotation.endswith('R180'):
            coords = (x - distance, y)
        elif rotation.endswith('R270'):
            coords = (x, y - distance)
        else:
            coords = (x + distance, y)

        if is_mirrored(rotation):
            x, y = coords
            coords = (-x, y)

        return coords


    def get_pin_label(self, pin, (null_x, null_y)):
        """ Return the Label for an eagle pin given the pin and the
        null point. """

        if not pin.name or pin.visible not in ('pin', 'both', None):
            return None

        rotation = make_angle(pin.rot or '0')

        ox, oy = rotate_point((5, 0), rotation, flip=is_mirrored(pin.rot))

        align = 'left'

        if rotation == 0.0:
            oy = -3
        elif rotation == 0.5:
            rotation = 1.5
            align = 'right'
            ox = 3
        elif rotation == 1.0:
            rotation = 0.0
            align = 'right'
            oy = -3
        elif rotation == 1.5:
            ox = 3

        return Label(null_x + ox, null_y + oy, pin.name,
                     align=align, rotation=rotation)


    def make_component_instances(self, root):
        """ Construct openjson component instances from an eagle model. """

        parts = dict((p.name, p) for p
                     in get_subattr(root, 'drawing.schematic.parts.part', ()))

        for sheet in get_subattr(root, 'drawing.schematic.sheets.sheet', ()):
            for instance in get_subattr(sheet, 'instances.instance', ()):
                inst = self.ensure_component_instance(parts, instance)
                self.set_symbol_attribute(instance, inst)


    def ensure_component_instance(self, parts, instance):
        """ Ensure there is a component instance for an eagle instance. """

        part = parts[instance.part]

        if part.name in self.part2inst:
            return self.part2inst[part.name]

        library_id = part.library + ':' + part.deviceset + ':' + part.device

        cpt = self.design.components.components[library_id]

        self.part2inst[part.name] = ComponentInstance(instance.part, part, library_id, 0)
        self.part2inst[part.name].add_attribute('refdes', part.name)

        # pre-create symbol attributes, to be filled in during
        # instance processing
        for _ in cpt.symbols[0].bodies:
            self.part2inst[part.name].add_symbol_attribute(
                SymbolAttribute(0, 0, 0.0, False))

        if part.value:
            self.part2inst[part.name].add_attribute('value', part.value)

        self.design.add_component_instance(self.part2inst[part.name])

        self.part2inst[part.name].add_attribute('eaglexml_device', part.device)

        return self.part2inst[part.name]


    def set_symbol_attribute(self, instance, openjson_inst):
        """ Fill out an openjson symbol attribute from an eagle instance
        and an openjson instance. """

        cpt = self.design.components.components[openjson_inst.library_id]

        attr = openjson_inst.symbol_attributes[self.cptgate2body_index[cpt, instance.gate]]

        attr.x = self.make_length(instance.x)
        attr.y = self.make_length(instance.y)
        attr.flip = is_mirrored(instance.rot)
        attr.rotation = make_angle(instance.rot or '0')

        if instance.smashed == 'yes':
            annotations = self.iter_instance_annotations(instance)
        else:
            annotations = sorted(self.cptgate2ann_map.get((cpt, instance.gate)).items())

        for name, ann in annotations:
            ann = Annotation('', ann.x, ann.y, ann.rotation, ann.visible)
            if name == 'name':
                ann.value = openjson_inst.instance_id
                if len(openjson_inst.symbol_attributes) > 1:
                    ann.value += instance.gate or ''
                attr.add_annotation(ann)
            elif name == 'value' and 'value' in openjson_inst.attributes:
                ann.value = openjson_inst.attributes['value']
                attr.add_annotation(ann)

        self.part2gate2symattr[instance.part][instance.gate] = attr


    def iter_instance_annotations(self, instance):
        """ Return an iterator over (name, Annotation) for the annotations
        in an eagle instance. """

        for ob in instance.attribute:
            name = ob.name.lower()
            x = self.make_length(ob.x) - self.make_length(instance.x)
            y = self.make_length(ob.y) - self.make_length(instance.y)
            rotation = make_angle('0' if ob.rot is None else ob.rot)
            ann = Annotation(ob.name, x, y, rotation, 'true')
            yield (name, ann)


    def make_nets(self, root):
        """ Construct openjson nets from an eagle model. """

        for sheet in get_subattr(root, 'drawing.schematic.sheets.sheet', ()):
            for net in get_subattr(sheet, 'nets.net', ()):
                self.design.add_net(self.make_net(net))


    def make_net(self, net):
        """ Construct an openjson net from an eagle net. """

        points = {} # (x, y) -> NetPoint

        def get_point(x, y):
            """ Return a new or existing NetPoint for an (x,y) point """
            if (x, y) not in points:
                points[x, y] = NetPoint('%da%d' % (x, y), x, y)
                out_net.add_point(points[x, y])
            return points[x, y]

        out_net = Net(net.name)

        for segment in get_subattr(net, 'segment', ()):
            for wire in get_subattr(segment, 'wire', ()):
                out_net.connect((get_point(self.make_length(wire.x1),
                                           self.make_length(wire.y1)),
                                 get_point(self.make_length(wire.x2),
                                           self.make_length(wire.y2))))

            for pinref in get_subattr(segment, 'pinref', ()):
                self.connect_pinref(pinref,
                                    get_point(*self.get_pinref_point(pinref)))

        return out_net


    def get_pinref_point(self, pinref):
        """ Return the (x, y) point of a pinref. """

        symattr = self.part2gate2symattr[pinref.part][pinref.gate]
        inst = self.part2inst[pinref.part]
        cpt = self.design.components.components[inst.library_id]
        pin = self.cptgate2pin_map[cpt, pinref.gate][pinref.pin]

        ox, oy = rotate_point((pin.p2.x, pin.p2.y), symattr.rotation,
                              flip=symattr.flip)

        return (symattr.x + ox, symattr.y + oy)


    def connect_pinref(self, pinref, point):
        """ Add a connected component to a NetPoint given an eagle pinref. """

        inst = self.part2inst[pinref.part]
        cpt = self.design.components.components[inst.library_id]
        pin = self.cptgate2pin_map[cpt, pinref.gate][pinref.pin]

        point.add_connected_component(
            ConnectedComponent(inst.instance_id, pin.pin_number))


    def make_length(self, value):
        """ Make an openjson length measurement from an eagle length. """

        return int(round(float(value) * self.MULT * self.SCALE))


def make_angle(value):
    """ Make an openjson angle measurement from an eagle angle. """

    angle = float(value.lstrip('MSR')) / 180

    return angle if angle == 0.0 else 2.0 - angle


def is_mirrored(rot):
    """
    Return True if an eaglexml rotation is mirrored.
    """

    return rot and rot.startswith('M')


def get_subattr(obj, name, default=None):
    """ Return an attribute given a dotted name, or the default if
    there is not attribute or the attribute is None. """

    for attr in name.split('.'):
        obj = getattr(obj, attr, None)

    return default if obj is None else obj


def rotate_point((x, y), angle, flip=False):
    """
    Return the point rotated by the given openjson angle (clockwise).
    If flip is True, then do a vertical flip around the y axis after
    the rotation.
    """

    rads = -angle * pi

    mat = [(int(cos(rads)), -int(sin(rads))),
           (int(sin(rads)), int(cos(rads)))]

    return ((x * mat[0][0] + y * mat[0][1]) * (-1 if flip else 1),
            x * mat[1][0] + y * mat[1][1])


class PinNumberAssignment(object):
    """
    An assignment of pin numbers to a collection of gates and pin names.
    """

    def __init__(self, device):
        self.gatepin2pad = {} # (gate name, pin name) -> pad number

        if device.connects is None:
            return

        for connect in device.connects.connect:
            self.gatepin2pad[connect.gate, connect.pin] = connect.pad

    def get_pin_number_lookup(self, gate_name):
        def lookup(pin_name):
            return self.gatepin2pad.get((gate_name, pin_name), pin_name)
        return lookup
